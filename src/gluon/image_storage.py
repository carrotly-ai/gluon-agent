"""Image storage service for managing file attachments.

Handles upload, storage, deduplication, and retrieval of images
for run tasks, with automatic copy to worktree for AI visibility.
"""

import hashlib
import shutil
from pathlib import Path
from typing import BinaryIO

from gluon.models import ImageAttachment
from gluon.store import GluonStore


class ImageStorageError(Exception):
    """Base exception for image storage operations."""

    pass


class ImageTooLargeError(ImageStorageError):
    """Raised when image exceeds maximum size."""

    def __init__(self, size_bytes: int, max_bytes: int):
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        size_mb = size_bytes / (1024 * 1024)
        max_mb = max_bytes / (1024 * 1024)
        super().__init__(f"Image is too large: {size_mb:.1f}MB. Maximum allowed: {max_mb:.1f}MB")


class InvalidImageFormatError(ImageStorageError):
    """Raised when image format is not supported."""

    def __init__(self, mime_type: str | None, allowed_types: set[str]):
        self.mime_type = mime_type
        self.allowed_types = allowed_types
        super().__init__(f"Invalid image format: {mime_type}. Supported formats: {', '.join(sorted(allowed_types))}")


class ImageNotFoundError(ImageStorageError):
    """Raised when image is not found."""

    def __init__(self, image_id: str):
        self.image_id = image_id
        super().__init__(f"Image not found: {image_id}")


# MIME type to extension mapping
MIME_TO_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


def get_extension_from_mime(mime_type: str | None) -> str:
    """Get file extension from MIME type."""
    if mime_type and mime_type in MIME_TO_EXT:
        return MIME_TO_EXT[mime_type]
    return ".bin"


def get_extension_from_filename(filename: str) -> str:
    """Get file extension from filename."""
    return Path(filename).suffix.lower() or ".bin"


class ImageStorageService:
    """
    Service for storing and managing image attachments.

    Features:
    - Content-based deduplication via SHA256 hash
    - Organized storage with hash-based subdirectories
    - Size and format validation
    - Automatic copy to worktree for AI visibility
    """

    STORAGE_DIR = Path.home() / ".gluon" / "images"
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
    ALLOWED_MIME_TYPES = {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/svg+xml",
    }

    def __init__(self, store: GluonStore | None = None):
        """Initialize the image storage service."""
        self.store = store or GluonStore()
        self.STORAGE_DIR.mkdir(parents=True, exist_ok=True)

    def _compute_hash(self, data: bytes) -> str:
        """Compute SHA256 hash of file content."""
        return hashlib.sha256(data).hexdigest()

    def _get_storage_path(self, hash_value: str, extension: str) -> Path:
        """Get storage path for a file based on its hash.

        Uses first 2 characters of hash as subdirectory to avoid
        too many files in a single directory.
        """
        subdir = hash_value[:2]
        filename = f"{hash_value}{extension}"
        return self.STORAGE_DIR / subdir / filename

    def _validate_file(
        self,
        data: bytes,
        mime_type: str | None,
        original_name: str,
    ) -> None:
        """Validate file size and type."""
        # Validate size
        if len(data) > self.MAX_FILE_SIZE:
            raise ImageTooLargeError(len(data), self.MAX_FILE_SIZE)

        # Validate MIME type if provided
        if mime_type and mime_type not in self.ALLOWED_MIME_TYPES:
            raise InvalidImageFormatError(mime_type, self.ALLOWED_MIME_TYPES)

        # If no MIME type, check extension
        if not mime_type:
            ext = get_extension_from_filename(original_name).lower()
            allowed_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
            if ext not in allowed_extensions:
                raise InvalidImageFormatError(ext, self.ALLOWED_MIME_TYPES)

    def save_image(
        self,
        data: bytes,
        original_name: str,
        mime_type: str | None = None,
    ) -> ImageAttachment:
        """
        Save an image to storage with deduplication.

        If an image with the same content hash already exists, returns
        the existing image metadata without storing a duplicate.

        Args:
            data: Image file content as bytes
            original_name: Original filename from user
            mime_type: MIME type (optional, will infer from extension)

        Returns:
            ImageAttachment with metadata

        Raises:
            ImageTooLargeError: If file exceeds MAX_FILE_SIZE
            InvalidImageFormatError: If file type is not supported
        """
        self._validate_file(data, mime_type, original_name)

        # Compute hash for deduplication
        content_hash = self._compute_hash(data)

        # Check if image already exists (deduplication)
        existing = self.store.get_image_by_hash(content_hash)
        if existing:
            return existing

        # Determine extension
        if mime_type:
            extension = get_extension_from_mime(mime_type)
        else:
            extension = get_extension_from_filename(original_name)

        # Get storage path
        storage_path = self._get_storage_path(content_hash, extension)

        # Create subdirectory if needed
        storage_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        storage_path.write_bytes(data)

        # Calculate relative path for storage
        relative_path = str(storage_path.relative_to(self.STORAGE_DIR))

        # Create image record
        image = ImageAttachment(
            file_path=relative_path,
            original_name=original_name,
            mime_type=mime_type,
            size_bytes=len(data),
            hash=content_hash,
        )

        # Save to database
        self.store.create_image(image)

        return image

    def save_image_from_file(
        self,
        file: BinaryIO,
        original_name: str,
        mime_type: str | None = None,
    ) -> ImageAttachment:
        """
        Save an image from a file-like object.

        Args:
            file: File-like object to read from
            original_name: Original filename
            mime_type: MIME type (optional)

        Returns:
            ImageAttachment with metadata
        """
        data = file.read()
        return self.save_image(data, original_name, mime_type)

    def get_image(self, image_id: str) -> ImageAttachment:
        """
        Get image metadata by ID.

        Args:
            image_id: Image UUID

        Returns:
            ImageAttachment

        Raises:
            ImageNotFoundError: If image not found
        """
        image = self.store.get_image(image_id)
        if not image:
            raise ImageNotFoundError(image_id)
        return image

    def get_image_data(self, image_id: str) -> tuple[bytes, ImageAttachment]:
        """
        Get image file content and metadata.

        Args:
            image_id: Image UUID

        Returns:
            Tuple of (file bytes, ImageAttachment)

        Raises:
            ImageNotFoundError: If image not found or file missing
        """
        image = self.get_image(image_id)
        full_path = self.STORAGE_DIR / image.file_path

        if not full_path.exists():
            raise ImageNotFoundError(image_id)

        return full_path.read_bytes(), image

    def delete_image(self, image_id: str) -> bool:
        """
        Delete an image from storage and database.

        Only deletes the file if no other references exist.

        Args:
            image_id: Image UUID

        Returns:
            True if deleted, False if not found
        """
        image = self.store.get_image(image_id)
        if not image:
            return False

        # Check if any runs still reference this image
        references = self.store.count_image_references(image_id)
        if references == 0:
            # Safe to delete file
            full_path = self.STORAGE_DIR / image.file_path
            if full_path.exists():
                full_path.unlink()

            # Clean up empty subdirectory
            if full_path.parent.exists() and not any(full_path.parent.iterdir()):
                full_path.parent.rmdir()

        # Delete from database
        return self.store.delete_image(image_id)

    def attach_to_run(self, run_id: str, image_id: str) -> None:
        """
        Attach an image to a run.

        Args:
            run_id: Run UUID
            image_id: Image UUID

        Raises:
            ImageNotFoundError: If image not found
        """
        image = self.store.get_image(image_id)
        if not image:
            raise ImageNotFoundError(image_id)

        self.store.attach_image_to_run(run_id, image_id)

    def detach_from_run(self, run_id: str, image_id: str) -> bool:
        """
        Detach an image from a run.

        Args:
            run_id: Run UUID
            image_id: Image UUID

        Returns:
            True if detached, False if not attached
        """
        return self.store.detach_image_from_run(run_id, image_id)

    def list_images_for_run(self, run_id: str) -> list[ImageAttachment]:
        """
        List all images attached to a run.

        Args:
            run_id: Run UUID

        Returns:
            List of ImageAttachment objects
        """
        return self.store.list_images_for_run(run_id)

    def copy_to_worktree(
        self,
        run_id: str,
        worktree_path: Path,
        target_dir: str = ".gluon-images",
    ) -> list[str]:
        """
        Copy all images for a run to the worktree directory.

        Creates a `.gluon-images/` directory in the worktree and copies
        all attached images there, making them visible to the AI agent.

        Args:
            run_id: Run UUID
            worktree_path: Path to the worktree root
            target_dir: Subdirectory name for images (default: .gluon-images)

        Returns:
            List of copied file paths (relative to worktree)
        """
        images = self.list_images_for_run(run_id)
        if not images:
            return []

        # Create target directory
        target_path = worktree_path / target_dir
        target_path.mkdir(parents=True, exist_ok=True)

        copied_paths = []
        for image in images:
            source_path = self.STORAGE_DIR / image.file_path
            if not source_path.exists():
                continue

            # Use original name for better AI understanding
            dest_path = target_path / image.original_name

            # Handle name conflicts by appending hash prefix
            if dest_path.exists():
                stem = dest_path.stem
                suffix = dest_path.suffix
                dest_path = target_path / f"{stem}_{image.hash[:8]}{suffix}"

            shutil.copy2(source_path, dest_path)
            relative_path = str(dest_path.relative_to(worktree_path))
            copied_paths.append(relative_path)

        return copied_paths

    def get_markdown_references(self, run_id: str) -> str:
        """
        Get markdown references for all images attached to a run.

        Returns a formatted string with markdown image references that
        can be appended to the prompt.

        Args:
            run_id: Run UUID

        Returns:
            Markdown string with image references
        """
        images = self.list_images_for_run(run_id)
        if not images:
            return ""

        lines = ["", "## Attached Images", ""]
        for image in images:
            lines.append(f"- ![{image.original_name}](.gluon-images/{image.original_name})")

        return "\n".join(lines)
