import json
import shutil
import tempfile
import unittest
from pathlib import Path

from testfucs.random_test.batch_random_modify import PROJECT_ROOT, batch_modify


class BatchReplacementTest(unittest.TestCase):
    def test_failed_scenario_is_replaced_by_arg_carcarana(self):
        source = (
            PROJECT_ROOT
            / "data"
            / "LGSVL-VSE"
            / "ARG_Carcarana-7_1_I-1-1.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            work_dir = Path(directory)
            original = work_dir / "unavailable_map.json"
            original.write_text(
                json.dumps({"map": {"name": "unavailable_map.xodr"}}),
                encoding="utf-8",
            )
            replacement = work_dir / source.name
            shutil.copy2(source, replacement)
            output_dir = work_dir / "output"
            progress_events = []

            results, failures = batch_modify(
                [original],
                ["width"],
                output_dir,
                progress_callback=progress_events.append,
            )

            self.assertEqual(failures, [])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["json"], str(replacement.resolve()))
            self.assertEqual(
                Path(results[0]["xodr"]).stem,
                "ARG_Carcarana-7_1_I-1-1",
            )
            self.assertEqual(results[0]["replacement_for"], str(original.resolve()))
            output_name = results[0]["output_name"]
            self.assertTrue((output_dir / f"{output_name}.xodr").is_file())
            output_json = output_dir / f"{output_name}.json"
            self.assertTrue(output_json.is_file())
            with output_json.open("r", encoding="utf-8") as file:
                self.assertEqual(json.load(file)["map"]["name"], output_name)
            self.assertEqual(progress_events[-1]["status"], "ok")
            self.assertEqual(
                progress_events[-1]["replacement_for"], str(original.resolve())
            )


if __name__ == "__main__":
    unittest.main()
