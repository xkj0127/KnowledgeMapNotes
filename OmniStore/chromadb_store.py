import json
from collections import defaultdict
import networkx as nx
import chromadb
from chromadb.utils import embedding_functions
class StoreTool:
    def __init__(self, storage_path="./chroma_data",embedding_function=None):
        # 初始化chromadb客户端
        self.client = chromadb.PersistentClient(path=storage_path)

        if embedding_function is None:
            # 使用默认的embedding函数（实际使用中可以替换）
            self.embedding_func = embedding_functions.DefaultEmbeddingFunction()
        else:
            from embedding_tools.embedding_tools import BgeZhEmbeddingFunction
            embedder = BgeZhEmbeddingFunction(
                model_path=r"D:\Models_Home\Huggingface\models--BAAI--bge-base-zh\snapshots\0e5f83d4895db7955e4cb9ed37ab73f7ded339b6",
                device="cuda"  # 可选，强制使用GPU
            )
            self.embedding_func =embedder

        # 获取或创建集合
        self.collection = self.client.get_or_create_collection(
            name="kg_states",
            embedding_function=self.embedding_func
        )
        # 分块后的文本向量化结果，进行向量查询
        self.vector_collection = self.client.get_or_create_collection(
            name="bolt_vectors",
            embedding_function=self.embedding_func
        )

    def save_state(self, kg_manager):
        """保存文本块向量到chromadb，便于rag使用"""
        bolt_count = len(kg_manager.Bolts)
        metadatas = []
        for bid, text in kg_manager.Bolts:
            entry_metadata = {
                "file": kg_manager.file,
                "operation_type": "add",
                "text_snippet": text[:50],
                "bolt_count": bolt_count,
                "original_file_type": kg_manager.original_file_type  # 存储原始文件名
            }
            metadatas.append(entry_metadata)

        self.vector_collection.upsert(
            ids=[bid for bid,text in kg_manager.Bolts],
            metadatas=metadatas,
            embeddings= self.embedding_func([text for bid,text in kg_manager.Bolts]),
            documents=[text for bid,text in kg_manager.Bolts]
        )

        """保存KgManager状态到chromadb"""
        # 序列化有向图
        graph_data = nx.node_link_data(kg_manager.current_G)

        # 准备需要存储的元数据
        metadata = {
            "file": kg_manager.file,
            "kg_triplet": json.dumps(kg_manager.kg_triplet),
            "bidirectional_mapping": json.dumps({
                "entity_to_label": dict(kg_manager.bidirectional_mapping["entity_to_label"]),
                "label_to_entities": dict(kg_manager.bidirectional_mapping["label_to_entities"])
            }),
            "current_G": json.dumps(graph_data),
            "Bolts": json.dumps(kg_manager.Bolts),
            "original_file_type": kg_manager.original_file_type  # 存储原始文件名
        }

        # 使用文件名作为ID，存入集合
        self.collection.upsert(
            ids=[kg_manager.file],
            metadatas=[metadata],
            documents=[kg_manager.file]  # 使用文件名作为文档内容
        )

    def load_state(self, filename):
        """从chromadb加载指定文件名的状态"""
        results = self.collection.get(ids=[filename])
        if not results["metadatas"]:
            return None

        metadata = results["metadatas"][0]

        # 反序列化数据
        return {
            "file": metadata["file"],
            "kg_triplet": json.loads(metadata["kg_triplet"]),
            "bidirectional_mapping": {
                "entity_to_label": dict(json.loads(metadata["bidirectional_mapping"])["entity_to_label"]),
                "label_to_entities": defaultdict(list,
                                                 json.loads(metadata["bidirectional_mapping"])["label_to_entities"])
            },
            "current_G": nx.node_link_graph(json.loads(metadata["current_G"])),
            "Bolts": json.loads(metadata["Bolts"]),
            "original_file_type": metadata.get("original_file_type", filename)  # 使用原始文件名
        }

    def delete_states(self, filenames: list):
        if not isinstance(filenames, list) or len(filenames) == 0:
            raise ValueError("filenames必须是非空列表")
        """批量删除多个文件状态"""
        self.vector_collection.delete(
            where={"file": {"$in": filenames}}
        )
        self.collection.delete(ids=filenames)
        return "delete_states success"


    def list_files(self):
        """获取所有存储的文件信息"""
        return self.collection.get()

    # 查询向量
    def select_vectors(self, query: str, file: str, n_results: int = 5):
        """查询指定文件中最相似的文本块

        Args:
            query: 查询文本
            file: 要过滤的文件名
            n_results: 返回结果数量

        Returns:
            {
                "ids": 结果ID列表,
                "documents": 文本内容列表,
                "metadatas": 元数据列表,
                "distances": 相似度分数列表
            }
        """
        # 生成查询向量
        query_embedding = self.embedding_func([query])

        # 执行带元数据过滤的相似度查询
        results = self.vector_collection.query(
            query_embeddings=query_embedding,
            where={"file": file},  # 元数据过滤
            n_results=n_results,
            include=["documents", "metadatas", "distances"]
        )

        # 标准化返回结构（处理ChromaDB返回的嵌套列表）
        return {
            "ids": results["ids"][0],  # 第一层列表对应不同query
            "documents": results["documents"][0],
            "metadatas": results["metadatas"][0],
            "distances": results["distances"][0]
        }