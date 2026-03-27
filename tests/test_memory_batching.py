import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from navig.memory.indexer import MemoryIndexer


class MockEmbeddingProvider:
    def __init__(self):
        self.batch_size = 5
        self.model_name = "mock-model"
        self.embed_calls = 0
        self.texts_embedded = 0

    def embed_batch(self, texts):
        self.embed_calls += 1
        self.texts_embedded += len(texts)
        # Return fake 10d vectors
        return [[0.1] * 10] * len(texts)


class TestMemoryBatching(unittest.TestCase):
    def setUp(self):
        self.storage = MagicMock()
        self.storage.file_needs_reindex.return_value = True
        self.storage.get_cached_embedding.return_value = None

        self.provider = MockEmbeddingProvider()
        self.indexer = MemoryIndexer(self.storage, self.provider)

    def test_batch_processing(self):
        # Create dummy directory structure
        with (
            patch("pathlib.Path.rglob") as mock_rglob,
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text"),
            patch("pathlib.Path.stat"),
        ):
            # Simulate 12 files
            mock_files = []
            for i in range(12):
                f = MagicMock()
                f.relative_to.return_value.as_posix.return_value = f"file_{i}.md"
                # Mock stat
                s = MagicMock()
                s.st_mtime = 123456
                f.stat.return_value = s
                # Mock read
                f.read_text.return_value = "Content " * 50
                mock_files.append(f)

            self.indexer._find_files = MagicMock(return_value=mock_files)
            self.indexer._compute_file_hash = MagicMock(return_value="hash")

            # Run index_directory
            result = self.indexer.index_directory(Path("/dummy"), force_reindex=True)

            # Check results
            self.assertEqual(result.files_processed, 12, "Should process 12 files")
            # 12 files -> 12 chunks. Batch size 5. -> 3 calls
            # However _index_file returns dict with 'chunks': 1
            # _chunk_text is generator.

            self.assertEqual(
                self.provider.embed_calls, 3, "Should batch calls (ceil(12/5)=3)"
            )

    def test_chunking_separation(self):
        p = MagicMock()
        p.read_text.return_value = "Valid text"
        p.exists.return_value = True
        p.stat.return_value.st_mtime = 123
        p.relative_to.return_value.as_posix.return_value = "single.md"
        p.parent = Path("/dummy")

        self.indexer._compute_file_hash = MagicMock(return_value="h")
        self.indexer._index_file = MagicMock(
            return_value={
                "chunks": 1,
                "tokens": 100,
                "embedded": 0,
                "chunks_obj": [MagicMock(content="text")],
            }
        )

        res = self.indexer.index_file(p)

        self.assertEqual(res.chunks_embedded, 1)


if __name__ == "__main__":
    unittest.main()
