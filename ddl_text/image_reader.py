"""Decode only static JPEG/PNG bytes; never resolve paths or URLs from input."""
import hashlib
import io
import math
import warnings
from dataclasses import dataclass

from .adapters import AgentError
from .media_worker import bounded


READER_VERSION = 'niuma-static-image/1'
IMAGE_MIMES = {'image/png', 'image/jpeg'}


@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int = 20 * 1024 * 1024
    max_pixels: int = 16_000_000
    max_encoded_bytes: int = 8 * 1024 * 1024
    decode_seconds: float = 10

    def __post_init__(self):
        for name in ('max_bytes', 'max_pixels', 'max_encoded_bytes'):
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= 20 * 1024 * 1024:
                raise ValueError('invalid_image_limit')
        if (isinstance(self.decode_seconds, bool) or not isinstance(self.decode_seconds, (int, float))
                or not math.isfinite(self.decode_seconds) or not 0 < self.decode_seconds <= 60):
            raise ValueError('invalid_decode_timeout')


class MetadataMismatch(ValueError):
    """Map to HTTP 400 asset_metadata_mismatch AFTER backend ownership checks."""

    def __init__(self):
        super().__init__('asset_metadata_mismatch')


def check_bytes(data, asset, limits):
    if not isinstance(data, bytes):
        raise AgentError('file_read_failed', False)
    if len(data) > limits.max_bytes:
        raise AgentError('limit_exceeded', False)
    if len(data) != asset['size_bytes'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
        raise MetadataMismatch()
    mime = ('image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else
            'image/jpeg' if data.startswith(b'\xff\xd8\xff') else None)
    if mime != asset['mime_type']:
        raise MetadataMismatch()


def _decode(data, limits):
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = limits.max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=['PNG', 'JPEG']) as original:
                if getattr(original, 'n_frames', 1) != 1:
                    raise AgentError('reader_unsupported', False)
                if original.width * original.height > limits.max_pixels:
                    raise AgentError('limit_exceeded', False)
                original.verify()
            with Image.open(io.BytesIO(data), formats=['PNG', 'JPEG']) as original:
                original.load()  # Reject truncated JPEG/PNG rather than silently repairing it.
                oriented = ImageOps.exif_transpose(original)
                # Preserve every pixel and orientation, but omit metadata, GPS, comments,
                # filenames and color profiles from the provider payload. No resize/crop.
                rgba = oriented.convert('RGBA')
                canvas = Image.new('RGB', rgba.size, 'white')
                canvas.paste(rgba, mask=rgba.getchannel('A'))
                output = io.BytesIO()
                canvas.save(output, format='PNG')
                raw = output.getvalue()
                if len(raw) > limits.max_encoded_bytes:
                    raise AgentError('limit_exceeded', False)
                return raw
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AgentError('limit_exceeded', False) from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise AgentError('file_read_failed', False) from None


def read_image(data, asset, limits):
    check_bytes(data, asset, limits)
    return bounded(_decode, (data, limits), limits.decode_seconds, 'file_read_failed')
