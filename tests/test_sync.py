import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_official_sources as sync


class SourceMonitorTests(unittest.TestCase):
    def test_only_https_and_allowlisted_hosts_are_accepted(self):
        hosts = ["gate2026.iitg.ac.in"]
        self.assertTrue(sync.host_allowed("https://gate2026.iitg.ac.in/papers/CS.pdf", hosts))
        self.assertTrue(sync.host_allowed("https://www.gate2026.iitg.ac.in/papers/CS.pdf", hosts))
        self.assertFalse(sync.host_allowed("http://gate2026.iitg.ac.in/papers/CS.pdf", hosts))
        self.assertFalse(sync.host_allowed("https://gate2026.iitg.ac.in.example.org/CS.pdf", hosts))
        self.assertFalse(sync.host_allowed("https://example.org/CS.pdf", hosts))

    def test_parse_links_resolves_urls_and_drops_fragments(self):
        links = sync.parse_links(
            '<a href="/papers/CS-1.pdf#page=2">CS-1</a><a href="https://example.org/a.pdf">External</a>',
            "https://gate2026.iitg.ac.in/download.html",
        )
        self.assertEqual(links[0]["url"], "https://gate2026.iitg.ac.in/papers/CS-1.pdf")
        self.assertEqual(links[0]["text"], "CS-1")
        self.assertEqual(links[1]["url"], "https://example.org/a.pdf")

    def test_classifier_distinguishes_answer_keys_and_papers(self):
        self.assertEqual(sync.classify_document("CS Answer Key", "https://gate2026.iitg.ac.in/CS-key.pdf"), "answer_key")
        self.assertEqual(sync.classify_document("Computer Science", "https://gate2026.iitg.ac.in/CS-1.pdf"), "question_paper_or_cs_resource")
        self.assertEqual(sync.classify_document("CS", "https://gate2026.iitg.ac.in/doc/download/Answer_keys2023/CS_ANS_GATE2023.pdf"), "answer_key")
        self.assertEqual(sync.classify_document("CS-1", "https://gate2026.iitg.ac.in/doc/download/2026/Keys/CS1_Keys.pdf"), "answer_key")
        self.assertEqual(sync.classify_document("BULK DOWNLOAD OF QUESTION PAPERS (2007 TO 2025)", "https://drive.google.com/drive/folders/example"), "bulk_archive")

    def test_candidate_file_filter_keeps_cs_and_bulk_archives(self):
        self.assertTrue(sync.is_candidate_file("CS-1", "https://gate2026.iitg.ac.in/papers/CS-1.pdf"))
        self.assertTrue(sync.is_candidate_file("Bulk download of question papers", "https://gate2026.iitg.ac.in/downloads/all-papers.zip"))
        self.assertTrue(sync.is_candidate_file("BULK DOWNLOAD OF QUESTION PAPERS (2007 TO 2025)", "https://drive.google.com/drive/folders/example"))
        self.assertFalse(sync.is_candidate_file("Electrical Engineering", "https://gate2026.iitg.ac.in/papers/EE.pdf"))
        self.assertFalse(sync.is_candidate_file("Information Brochure", "https://gate2026.iitg.ac.in/brochure.pdf"))

    def test_url_metadata_change_is_only_reported_when_comparable(self):
        self.assertTrue(sync.metadata_changed({"etag": '"one"'}, {"etag": '"two"'}))
        self.assertFalse(sync.metadata_changed({"etag": None}, {"etag": '"two"'}))
        self.assertFalse(sync.metadata_changed({"content_length": 100}, {"content_length": 100}))
        self.assertTrue(sync.metadata_changed({"year_hint": 2025}, {"year_hint": 2024}))
        self.assertTrue(sync.metadata_changed({"document_type": "question_paper_or_cs_resource"}, {"document_type": "answer_key"}))


if __name__ == "__main__":
    unittest.main()
