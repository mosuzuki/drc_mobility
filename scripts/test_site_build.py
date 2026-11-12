import tempfile
import unittest
from pathlib import Path
from build_site import build

class PublicBuildTests(unittest.TestCase):
    def test_public_dependencies_without_source_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=build(tmp)
            self.assertTrue((target/'index.html').is_file())
            self.assertTrue((target/'assets/app.js').is_file())
            self.assertTrue((target/'data/report_summary.csv').is_file())
            self.assertTrue((target/'data/short_term_ml_forecast.json').is_file())
            self.assertFalse((target/'raw').exists())
            self.assertFalse((target/'extracted').exists())
            self.assertFalse((target/'scripts').exists())

if __name__=='__main__':unittest.main()
