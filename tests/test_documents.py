"""Tests de conversión de imágenes/JSON del visor a PDF (sin navegador)."""

import base64
import unittest
from io import BytesIO

from PIL import Image

from core.documents import image_from_json, images_to_pdf, is_pdf, page_count_from_json, to_pdf


def _png(color="red"):
    b = BytesIO(); Image.new("RGB", (50, 60), color).save(b, "PNG"); return b.getvalue()


class DocumentsTest(unittest.TestCase):
    def test_image_from_json_data_uri_nested(self):
        payload = {"page": 1, "render": {"img": "data:image/png;base64," + base64.b64encode(_png()).decode()}}
        self.assertEqual(image_from_json(payload), _png())

    def test_image_from_json_plain_base64(self):
        self.assertTrue(image_from_json({"data": base64.b64encode(_png()).decode()}))

    def test_image_from_json_without_image(self):
        self.assertIsNone(image_from_json({"text": "hola", "items": [1, 2]}))

    def test_page_count(self):
        self.assertEqual(page_count_from_json({"DocInfo": {"PageCount": 4}}), 4)
        self.assertEqual(page_count_from_json({"totalPages": 3}), 3)
        self.assertIsNone(page_count_from_json({"width": 800}))

    def test_images_to_pdf_multipage_tiff(self):
        b = BytesIO()
        Image.new("RGB", (50, 60), "red").save(b, "TIFF", save_all=True,
                                               append_images=[Image.new("RGB", (50, 60), "blue")])
        pdf = to_pdf(b.getvalue())
        self.assertTrue(is_pdf(pdf))
        self.assertGreaterEqual(pdf.count(b"/Type /Page"), 2)

    def test_pdf_passthrough_and_unknown(self):
        self.assertEqual(to_pdf(b"%PDF-1.7 x"), b"%PDF-1.7 x")
        self.assertIsNone(to_pdf(b"<html>"))
        self.assertTrue(is_pdf(images_to_pdf([_png(), _png("blue")])))


if __name__ == "__main__":
    unittest.main()
