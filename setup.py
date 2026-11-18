from setuptools import setup
from setuptools_rust import Binding, RustExtension

setup(
    rust_extensions=[
        RustExtension(
            "mini_aiohttp._native",
            path="rust/Cargo.toml",
            binding=Binding.PyO3,
            optional=True,
        )
    ],
    zip_safe=False,
)
