# -*- coding: utf-8 -*-
from django.core.management.base import BaseCommand
from monitor.services.knowledge_service import knowledge_service

class Command(BaseCommand):
    help = '扫描 Document/ 目录下的 Markdown 文件并构建 FAISS 向量索引库 (V3.5.0 RAG)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('正在启动 RAG 索引构建任务...'))
        try:
            knowledge_service.build_index()
            self.stdout.write(self.style.SUCCESS(f'成功！索引已保存至: {knowledge_service.INDEX_PATH}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'索引构建失败: {e}'))
