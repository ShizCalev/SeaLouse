import os
import tempfile
from contextlib import contextmanager


@contextmanager
def atomic_output(path):
    # don't overwrite the original until we've verified successful export
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w+b', dir=os.path.dirname(os.path.abspath(path)), prefix='.sealouse-', delete=False) as stream:
            temporary = stream.name
            yield stream
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)
