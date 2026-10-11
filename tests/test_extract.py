import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extract_cs_questions as extract


class QuestionExtractionTests(unittest.TestCase):
    def test_split_questions_and_skip_range_banner(self):
        text = (
            "General instructions\n"
            "Q.1 - Q.5 Carry ONE mark each\n"
            "Q. 1 Select the correct statement. (A) Alpha (B) Beta (C) Gamma (D) Delta\n"
            "Q. 2 Compute 2 + 3. (A) 2 (B) 3 (C) 5 (D) 6\n"
        )
        found = extract.split_questions(text)
        self.assertEqual(set(found), {1, 2})
        self.assertIn("Select the correct statement", found[1])
        self.assertIn("Compute 2 + 3", found[2])

    def test_question_body_extracts_contiguous_options(self):
        stem, options, warnings = extract.parse_question_body(
            "Which item is correct?\n(A) First option\n(B) Second option\n(C) Third option\n(D) Fourth option"
        )
        self.assertEqual(stem, "Which item is correct?")
        self.assertEqual([item["id"] for item in options], ["A", "B", "C", "D"])
        self.assertFalse(warnings)

    def test_body_without_detected_options_is_explicitly_flagged(self):
        stem, options, warnings = extract.parse_question_body("Read the network diagram and find the output.")
        self.assertTrue(stem)
        self.assertEqual(options, [])
        self.assertTrue(warnings)

    def test_answer_key_text_rows_are_parsed(self):
        rows = extract.parse_answer_key_text(
            "Q No Session Question Type Section Key/Range Marks\n"
            "1 1 MCQ GA B 1\n"
            "12 1 MSQ CS A, C 2\n"
            "33 2 NAT CS 3.25 to 3.75 2\n"
        )
        self.assertEqual(rows[1]["keyRange"], "B")
        self.assertEqual(rows[12]["questionType"], "MSQ")
        self.assertEqual(rows[33]["keyRange"], "3.25 to 3.75")

    def test_answer_values_preserve_ranges_and_types(self):
        self.assertEqual(extract.key_answer_value({"questionType": "MCQ", "keyRange": "b"}), "B")
        self.assertEqual(extract.key_answer_value({"questionType": "MSQ", "keyRange": "A, C"}), ["A", "C"])
        self.assertEqual(extract.key_answer_value({"questionType": "NAT", "keyRange": "2.5"}), 2.5)
        self.assertEqual(extract.key_answer_value({"questionType": "NAT", "keyRange": "2.0 to 3.0"}), "2 to 3")

    def test_infer_year_and_paper_code_for_official_names(self):
        self.assertEqual(extract.infer_year({"title": "Computer Science", "url": "https://gate2026.iitg.ac.in/doc/download/2026/QPs/CS1.pdf"}), 2026)
        self.assertEqual(extract.infer_paper_code({"title": "CS-1", "url": "https://gate2026.iitg.ac.in/doc/download/2026/Keys/CS1_Keys.pdf"}), "CS-1")
        self.assertEqual(extract.infer_paper_code({"title": "Computer Science", "url": "https://gate2026.iitg.ac.in/doc/download/2023/cs_2023.pdf"}), "CS")

    def test_only_unique_same_year_paper_key_pairs_are_selected(self):
        docs = [
            {"title": "Computer Science", "url": "https://gate2026.iitg.ac.in/2026/QPs/CS1.pdf", "year_hint": 2026, "document_type": "question_paper_or_cs_resource"},
            {"title": "CS-1", "url": "https://gate2026.iitg.ac.in/2026/Keys/CS1_Keys.pdf", "year_hint": 2026, "document_type": "answer_key"},
            {"title": "Computer Science", "url": "https://gate2026.iitg.ac.in/2025/CS12025.pdf", "year_hint": 2025, "document_type": "question_paper_or_cs_resource"},
        ]
        pairs, issues = extract.pair_papers_and_keys(docs, ["gate2026.iitg.ac.in"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0]["_year"], 2026)
        self.assertEqual(len(issues), 1)
        self.assertIn("No unique matching", issues[0]["issue"])

    def test_untrusted_or_http_pdf_is_rejected(self):
        self.assertFalse(extract.trusted_https("http://gate2026.iitg.ac.in/cs.pdf", ["gate2026.iitg.ac.in"]))
        self.assertFalse(extract.trusted_https("https://gate2026.iitg.ac.in.example.org/cs.pdf", ["gate2026.iitg.ac.in"]))
        self.assertTrue(extract.trusted_https("https://gate2026.iitg.ac.in/cs.pdf", ["gate2026.iitg.ac.in"]))


if __name__ == "__main__":
    unittest.main()
