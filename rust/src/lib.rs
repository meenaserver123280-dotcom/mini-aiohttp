use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};

fn latin1(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| char::from(*byte)).collect()
}

fn is_token(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&byte)
}

fn parse_inner(data: &[u8]) -> Result<(String, String, String, Vec<(String, String)>), &'static str> {
    let body = data.strip_suffix(b"\r\n\r\n").ok_or("Malformed request head")?;
    if body.windows(2).any(|pair| pair[1] == b'\n' && pair[0] != b'\r') {
        return Err("Malformed request head");
    }
    let mut lines = body.split(|byte| *byte == b'\n')
        .map(|line| line.strip_suffix(b"\r").unwrap_or(line));
    let mut first = lines.next().unwrap_or_default();
    while first.is_empty() {
        first = lines.next().unwrap_or_default();
    }
    if first.is_empty() {
        return Err("Malformed request line");
    }
    if first.len() + 2 > 8 * 1024 {
        return Err("Line too long");
    }

    let mut parts = first.split(|byte| *byte == b' ');
    let method = parts.next().unwrap_or_default();
    let target = parts.next().unwrap_or_default();
    let version = parts.next().unwrap_or_default();
    if method.is_empty() || method.len() > 20 || !method.iter().all(|byte| is_token(*byte)) {
        return Err("Invalid request method");
    }
    if target.is_empty() || version.is_empty() || parts.next().is_some() {
        return Err("Malformed request line");
    }

    let mut headers = Vec::new();
    let mut header_count = 0usize;
    for raw in lines {
        if raw.is_empty() {
            continue;
        }
        header_count += 1;
        if header_count > 100 {
            return Err("Too many headers");
        }
        if raw.len() + 2 > 8 * 1024 {
            return Err("Line too long");
        }
        if matches!(raw.first(), Some(b' ' | b'\t')) {
            return Err("Obsolete line folding is not allowed");
        }
        let colon = raw.iter().position(|byte| *byte == b':').ok_or("Malformed header")?;
        let name = &raw[..colon];
        let value = &raw[colon + 1..];
        if name.is_empty() || !name.iter().all(|byte| is_token(*byte)) {
            return Err("Malformed header");
        }
        if value.contains(&0) {
            return Err("Invalid header value");
        }
        let trimmed = value.iter().copied()
            .skip_while(|byte| *byte == b' ' || *byte == b'\t')
            .collect::<Vec<u8>>();
        let end = trimmed.iter().rposition(|byte| *byte != b' ' && *byte != b'\t')
            .map(|index| index + 1).unwrap_or(0);
        headers.push((latin1(name), latin1(&trimmed[..end])));
    }

    Ok((latin1(method), latin1(target), latin1(version), headers))
}

#[pyfunction]
fn parse_request_head(data: &Bound<'_, PyBytes>) -> PyResult<(String, String, String, Vec<(String, String)>)> {
    parse_inner(data.as_bytes()).map_err(PyValueError::new_err)
}


#[pyfunction]
fn serialize_response(
    py: Python<'_>,
    status: i64,
    reason: &str,
    headers: &Bound<'_, PyDict>,
    cookies: &Bound<'_, PyList>,
    body: &Bound<'_, PyBytes>,
    keep_alive: bool,
) -> PyResult<Py<PyBytes>> {
    let mut output = Vec::with_capacity(body.as_bytes().len() + 256);
    output.extend_from_slice(format!("HTTP/1.1 {} {}\\r\\n", status, reason).as_bytes());
    for (key, value) in headers.iter() {
        let name: String = key.extract()?;
        let value_bound = value.str()?;
        let value = value_bound.to_str()?.to_owned();
        let mut title = Vec::with_capacity(name.len());
        let mut upper = true;
        for byte in name.bytes() {
            if byte == b'-' {
                title.push(byte);
                upper = true;
            } else {
                title.push(if upper { byte.to_ascii_uppercase() } else { byte });
                upper = false;
            }
        }
        output.extend_from_slice(&title);
        output.extend_from_slice(b": ");
        output.extend_from_slice(value.as_bytes());
        output.extend_from_slice(b"\\r\\n");
    }
    for cookie in cookies.iter() {
        let value_bound = cookie.str()?;
        let value = value_bound.to_str()?.to_owned();
        output.extend_from_slice(b"Set-Cookie: ");
        output.extend_from_slice(value.as_bytes());
        output.extend_from_slice(b"\\r\\n");
    }
    output.extend_from_slice(b"\\r\\n");
    output.extend_from_slice(body.as_bytes());
    let _ = keep_alive;
    Ok(PyBytes::new(py, &output).unbind())
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse_request_head, module)?)?;
    module.add_function(wrap_pyfunction!(serialize_response, module)?)?;
    Ok(())
}