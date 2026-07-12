"""测试配置：使用独立临时数据库，避免清理用户数据。"""
from __future__ import annotations

import tempfile

from app.config import settings

# pytest 会先加载 conftest，再 import 测试模块；
# 这里把 db_path 指向临时文件，确保测试不会动到用户的 novel.db。
_test_db_path = tempfile.mktemp(suffix=".db")
settings.db_path = _test_db_path
