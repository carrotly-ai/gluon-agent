"""Unit tests for ImageStorageService.

Uses tmp_path fixtures for all file I/O — never touches real ~/.gluon/images.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gluon.image_storage import (
    ImageNotFoundError,
    ImageStorageService,
    ImageTooLargeError,
    InvalidImageFormatError,
    get_extension_from_filename,
    get_extension_from_mime,
)
from gluon.store import GluonStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(store: GluonStore):
    """Create a project so FK constraints are satisfied for create_run."""
    return store.create_project("test-proj", "/tmp/test-proj")


@pytest.fixture
def service(store: GluonStore, tmp_path: Path) -> ImageStorageService:
    """ImageStorageService with STORAGE_DIR pointed at tmp_path."""
    svc = ImageStorageService(store=store)
    svc.STORAGE_DIR = tmp_path / "images"
    svc.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return svc


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ===================================================================
# get_extension helpers
# ===================================================================


class TestExtensionHelpers:
    def test_mime_png(self):
        assert get_extension_from_mime("image/png") == ".png"

    def test_mime_jpeg(self):
        assert get_extension_from_mime("image/jpeg") == ".jpg"

    def test_mime_gif(self):
        assert get_extension_from_mime("image/gif") == ".gif"

    def test_mime_webp(self):
        assert get_extension_from_mime("image/webp") == ".webp"

    def test_mime_svg(self):
        assert get_extension_from_mime("image/svg+xml") == ".svg"

    def test_mime_unknown(self):
        assert get_extension_from_mime("application/pdf") == ".bin"

    def test_mime_none(self):
        assert get_extension_from_mime(None) == ".bin"

    def test_filename_extension(self):
        assert get_extension_from_filename("photo.jpg") == ".jpg"

    def test_filename_no_extension(self):
        assert get_extension_from_filename("noext") == ".bin"


# ===================================================================
# Validation
# ===================================================================


class TestValidation:
    def test_too_large(self, service):
        big_data = b"x" * (service.MAX_FILE_SIZE + 1)
        with pytest.raises(ImageTooLargeError):
            service.save_image(big_data, "big.png", "image/png")

    def test_invalid_mime_type(self, service):
        with pytest.raises(InvalidImageFormatError):
            service.save_image(b"data", "file.pdf", "application/pdf")

    def test_invalid_extension_no_mime(self, service):
        with pytest.raises(InvalidImageFormatError):
            service.save_image(b"data", "file.exe")

    def test_valid_extension_no_mime(self, service):
        # Should not raise — .png extension is allowed
        image = service.save_image(TINY_PNG, "test.png")
        assert image.original_name == "test.png"


# ===================================================================
# Save and deduplication
# ===================================================================


class TestSaveImage:
    def test_save_image(self, service):
        image = service.save_image(TINY_PNG, "screenshot.png", "image/png")
        assert image.original_name == "screenshot.png"
        assert image.size_bytes == len(TINY_PNG)
        assert image.hash is not None
        assert len(image.hash) == 64  # SHA256

    def test_deduplication(self, service):
        img1 = service.save_image(TINY_PNG, "first.png", "image/png")
        img2 = service.save_image(TINY_PNG, "second.png", "image/png")
        # Same content → same image returned
        assert img1.id == img2.id

    def test_different_content_different_images(self, service):
        img1 = service.save_image(TINY_PNG, "a.png", "image/png")
        img2 = service.save_image(TINY_PNG + b"\x00", "b.png", "image/png")
        assert img1.id != img2.id

    def test_file_written_to_disk(self, service):
        image = service.save_image(TINY_PNG, "disk.png", "image/png")
        full_path = service.STORAGE_DIR / image.file_path
        assert full_path.exists()
        assert full_path.read_bytes() == TINY_PNG


# ===================================================================
# Get / retrieve
# ===================================================================


class TestGetImage:
    def test_get_image(self, service):
        image = service.save_image(TINY_PNG, "test.png", "image/png")
        retrieved = service.get_image(image.id)
        assert retrieved.id == image.id

    def test_get_image_not_found(self, service):
        with pytest.raises(ImageNotFoundError):
            service.get_image("nonexistent-id")

    def test_get_image_data(self, service):
        image = service.save_image(TINY_PNG, "test.png", "image/png")
        data, meta = service.get_image_data(image.id)
        assert data == TINY_PNG
        assert meta.id == image.id


# ===================================================================
# Delete
# ===================================================================


class TestDeleteImage:
    def test_delete_image(self, service):
        image = service.save_image(TINY_PNG, "del.png", "image/png")
        full_path = service.STORAGE_DIR / image.file_path

        result = service.delete_image(image.id)
        assert result is True
        # File should be gone (no references)
        assert not full_path.exists()

    def test_delete_nonexistent(self, service):
        result = service.delete_image("nonexistent-id")
        assert result is False


# ===================================================================
# Attach / detach / list
# ===================================================================


class TestAttachDetach:
    def test_attach_and_list(self, service, store, project):
        image = service.save_image(TINY_PNG, "attach.png", "image/png")
        run = store.create_run(project_id=project.id, prompt="test")

        service.attach_to_run(run.id, image.id)
        images = service.list_images_for_run(run.id)
        assert len(images) == 1
        assert images[0].id == image.id

    def test_attach_not_found(self, service, store, project):
        run = store.create_run(project_id=project.id, prompt="test")
        with pytest.raises(ImageNotFoundError):
            service.attach_to_run(run.id, "bad-image-id")

    def test_detach(self, service, store, project):
        image = service.save_image(TINY_PNG, "detach.png", "image/png")
        run = store.create_run(project_id=project.id, prompt="test")

        service.attach_to_run(run.id, image.id)
        result = service.detach_from_run(run.id, image.id)
        assert result is True

        images = service.list_images_for_run(run.id)
        assert len(images) == 0

    def test_list_empty(self, service, store, project):
        run = store.create_run(project_id=project.id, prompt="test")
        assert service.list_images_for_run(run.id) == []
