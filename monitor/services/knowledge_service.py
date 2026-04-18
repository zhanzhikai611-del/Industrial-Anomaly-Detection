# -*- coding: utf-8 -*-
import os
import json
import logging
import numpy as np
import faiss
from pathlib import Path
from openai import OpenAI
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# 配置参数 (与 AgentService 保持一致)
API_KEY = "sk-6244491a10cd439b9d9013b557450741"
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_MODEL = "text-embedding-v2"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

class KnowledgeService:
    """
    V3.5.0 RAG 检索服务
    支持本地 Markdown 文档的向量化、存储与语义检索。
    """
    
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    DOCS_DIR = BASE_DIR / "Document"
    INDEX_PATH = BASE_DIR / "monitor" / "knowledge_base" / "index.faiss"
    METADATA_PATH = BASE_DIR / "monitor" / "knowledge_base" / "metadata.json"

    def __init__(self):
        self.index = None
        self.metadata = []
        self._ensure_dir()
        self.load_index()

    def _ensure_dir(self):
        """确保索引目录存在"""
        os.makedirs(self.INDEX_PATH.parent, exist_ok=True)

    def load_index(self):
        """加载本地向量库"""
        if self.INDEX_PATH.exists() and self.METADATA_PATH.exists():
            try:
                self.index = faiss.read_index(str(self.INDEX_PATH))
                with open(self.METADATA_PATH, 'r', encoding='utf-8') as f:
                    self.metadata = json.load(f)
                logger.info(f"[RAG] 索引加载成功，包含 {len(self.metadata)} 个知识分片。")
            except Exception as e:
                logger.error(f"[RAG] 索引加载失败: {e}")

    def get_embedding(self, text: str) -> List[float]:
        """获取文本向量"""
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text
        )
        return response.data[0].embedding

    def build_index(self):
        """全量构建索引 (扫描 Document 目录下的 .md 文件)"""
        logger.info("[RAG] 开始构建知识库索引...")
        all_chunks = []
        
        # 1. 扫描并解析文档
        md_files = list(self.DOCS_DIR.glob("*.md"))
        for md_file in md_files:
            try:
                content = md_file.read_text(encoding='utf-8')
                chunks = self._split_text(content, filename=md_file.name)
                all_chunks.extend(chunks)
            except Exception as e:
                logger.warning(f"[RAG] 无法读取文件 {md_file.name}: {e}")

        if not all_chunks:
            logger.warning("[RAG] 未找到有效的文档分片。")
            return

        # 2. 向量化
        embeddings = []
        for i, chunk in enumerate(all_chunks):
            try:
                emb = self.get_embedding(chunk['content'])
                embeddings.append(emb)
                if i % 10 == 0:
                    logger.info(f"[RAG] 已完成 {i}/{len(all_chunks)} 个切片的向量化...")
            except Exception as e:
                logger.error(f"[RAG] 向量化失败: {e}")

        # 3. 写入 FAISS
        dim = 1536
        embeddings_np = np.array(embeddings).astype('float32')
        index = faiss.IndexFlatL2(dim)
        index.add(embeddings_np)
        
        # 4. 持久化
        faiss.write_index(index, str(self.INDEX_PATH))
        with open(self.METADATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(all_chunks, f, ensure_ascii=False, indent=2)
        
        self.index = index
        self.metadata = all_chunks
        logger.info(f"[RAG] 索引构建完成！共计 {len(all_chunks)} 个切片。")

    def _split_text(self, text: str, filename: str, chunk_size=600, overlap=100) -> List[Dict[str, Any]]:
        """简单的 Markdown 文本切片逻辑"""
        chunks = []
        # 清理简单噪音
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        clean_text = "\n".join(lines)
        
        start = 0
        while start < len(clean_text):
            end = start + chunk_size
            content = clean_text[start:end]
            chunks.append({
                "content": content,
                "source": filename
            })
            start += (chunk_size - overlap)
        return chunks

    def search(self, query: str, top_k=3) -> str:
        """执行语义搜索"""
        if self.index is None or not self.metadata:
            return ""

        try:
            query_vector = self.get_embedding(query)
            query_vector_np = np.array([query_vector]).astype('float32')
            
            distances, indices = self.index.search(query_vector_np, top_k)
            
            results = []
            for idx, dist in zip(indices[0], distances[0]):
                if idx < len(self.metadata) and dist < 1.0: # 阈值过滤
                    chunk = self.metadata[idx]
                    results.append(f"【来源: {chunk['source']}】\n{chunk['content']}")
            
            return "\n\n".join(results)
        except Exception as e:
            logger.error(f"[RAG] 搜索异常: {e}")
            return ""

# 单例模式
knowledge_service = KnowledgeService()
