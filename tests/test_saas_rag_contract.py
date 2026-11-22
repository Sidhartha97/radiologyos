"""Guard the user's explicitly protected RAG implementation during SaaS work.

Fingerprints recorded from the before-saas implementation. AST hashing allows
Python whitespace changes, but changing generation logic requires explicit review.
"""
import ast
import hashlib
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FUNCTIONS={
    'rag_prompt':'b304997ae3ec84aa4b99ffea140b3a3e4fd16728ce4a2aacb47f74f4c92af6a3',
    'rag_retrieval_query':'975bd0d72b78af09ff5ccdaaee82b9b0f2e0bd8cce4556e03b8e1ecaba6167ba',
    'rag_generate':'bedaada914a377e1c504fe527068570b8f1831ce46cbce2c29d65234e9d850bd',
}
FILES={
    'ingestion/retrieval.py':'4099b1a72bf653e8627b5322179348b56353918397d42e10765c5c3fff63e21a',
    'ingestion/scope.py':'5726db59d87b0b62297daee86c994a649763b430644aa76583e93ff52d3480b7',
    'ingestion/documents.py':'c724082ce7cfecac83ee6b25daa52d3517a55e8b4c7250d961a1cba93f70ebe5',
}


class ProtectedRAGTests(unittest.TestCase):
    def test_generation_prompts_and_model_request_remain_unchanged(self):
        tree=ast.parse((ROOT/'server.py').read_text(encoding='utf-8'))
        functions={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
        for name,expected in FUNCTIONS.items():
            with self.subTest(function=name):
                self.assertEqual(hashlib.sha256(ast.dump(functions[name],include_attributes=False).encode()).hexdigest(),expected,
                                 'Protected RAG implementation changed; request separate approval.')

    def test_retrieval_scope_and_document_processing_remain_unchanged(self):
        for path,expected in FILES.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((ROOT/path).read_bytes().replace(b'\r\n',b'\n')).hexdigest(),expected)
