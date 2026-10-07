from django.test import TestCase
from django.urls import reverse


class ViewerPageTests(TestCase):
    def test_viewer_page_loads(self):
        response = self.client.get(reverse("viewer"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a file or drop it here")