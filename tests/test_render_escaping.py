"""
render() cannot be broken out of by a string in DATA / CFG (#16). Run from the
repo root:

    .venv/bin/python -m unittest discover tests
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import render  # noqa: E402


class RenderCannotBeBrokenOutOf(unittest.TestCase):
    HOSTILE = {"s": "</script><!-- <script> __CFG__ __FOOTER_META__", "n": 1.5}

    def setUp(self):
        self.html = render.render(self.HOSTILE, {"k": "</SCRIPT>", "footer_meta": "<b>1 h</b>"})

    def _blob(self, name):
        return re.search(r"const %s = (.*);\n" % name, self.html).group(1)

    def test_no_markup_opener_survives_inside_the_json(self):
        for name in ("DATA", "CFG"):
            self.assertNotIn("<", self._blob(name))

    def test_the_objects_round_trip(self):
        self.assertEqual(json.loads(self._blob("DATA")), self.HOSTILE)
        self.assertEqual(json.loads(self._blob("CFG"))["k"], "</SCRIPT>")

    def test_the_footer_is_text(self):
        self.assertIn("&lt;b&gt;1 h&lt;/b&gt;", self.html)


if __name__ == "__main__":
    unittest.main()
