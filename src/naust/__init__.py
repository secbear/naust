from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("naust")
except PackageNotFoundError:  # running from source, not installed
    __version__ = "0.0.0+unknown"
