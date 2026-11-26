"""Rules for uploaded files: size limit, no programs or scripts, safe file names. Pure functions."""
import re, unicodedata

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_RECORD = 50
# Programs, scripts and web documents: refused (the extension after the last dot decides).
BLOCKED_EXTENSIONS = frozenset("""
exe dll bat cmd com scr msi msp sys cpl pif lnk reg hta wsf wsh vbs vbe jse js mjs cjs ps1 psm1 psd1 sh bash zsh csh ksh fish command
jar app apk ipa dmg pkg deb rpm bin run appimage html htm xhtml shtml svg xht swf php phtml php3 php4 php5 phar asp aspx jsp py pyc pyw pl rb
""".split())


# A File field set to "pictures only" takes these (HEIC is what phones save; the browser may not be able to show it, but it is a photo).
PICTURE_EXTENSIONS = frozenset("jpg jpeg png webp gif avif heic heif tif tiff bmp".split())


class UploadError(ValueError):
    pass


def safe_name(name):
    """A harmless file name: no directories, no control characters, no odd symbols, never empty, at most 120 characters."""
    name = (name or "").replace("\\", "/").split("/")[-1]                 # drop any directory part (both separator styles)
    name = unicodedata.normalize("NFC", name)
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")  # control and format characters
    name = re.sub(r"[^\w .()+,\-]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem.strip(" .") or "file"
    ext = ext[:20]
    stem = stem[: 120 - len(ext) - 1] if ext else stem[:120]
    return f"{stem}.{ext}" if ext else stem


def check(name, size, pictures_only=False):
    """Raise UploadError with a sentence for people when the file may not be stored."""
    ext = name.rpartition(".")[2].lower() if "." in name else ""
    if pictures_only and ext not in PICTURE_EXTENSIONS:
        raise UploadError("this field takes pictures only (jpg, png, webp, gif, avif, heic, tif, bmp)")
    if ext in BLOCKED_EXTENSIONS:
        raise UploadError(f"programs and scripts cannot be uploaded (.{ext})")
    if size == 0:
        raise UploadError("the file is empty")
    if size > MAX_UPLOAD_BYTES:
        raise UploadError(f"the file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")


def unique_name(name, taken):
    """name, or name-2, name-3 ... (before the extension) so that nothing is overwritten. `taken` compares case-insensitively."""
    low = {t.lower() for t in taken}
    if name.lower() not in low:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    n = 2
    while True:
        cand = f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}"
        if cand.lower() not in low:
            return cand
        n += 1


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
