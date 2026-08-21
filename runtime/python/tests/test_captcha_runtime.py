import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "mia_crawl_service"))

from app.captcha.solver import CaptchaSolver


class CaptchaRuntimeTests(unittest.TestCase):
    def test_packaged_resvg_renderer_keeps_model_inference_on_solver(self):
        svg = (
            '<svg xmlns="ht' + 'tp://www.w3.org/2000/svg" width="160" height="50">'
            '<rect width="160" height="50" fill="white"/>'
            '<text x="10" y="35" font-size="30">12345</text>'
            '</svg>'
        )

        def solve_twice():
            solver = CaptchaSolver()
            return solver.solve(svg), solver.solve(svg)

        # Production authentication runs in the background worker, not the
        # runtime JSON-RPC main thread.
        with ThreadPoolExecutor(max_workers=1) as executor:
            results = executor.submit(solve_twice).result(timeout=30)
        self.assertTrue(all(isinstance(item, str) and item for item in results))


if __name__ == "__main__":
    unittest.main()
