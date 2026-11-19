"""Print the package version from pyproject.toml."""
import tomllib

with open("pyproject.toml", "rb") as handle:
    print(tomllib.load(handle)["project"]["version"])
