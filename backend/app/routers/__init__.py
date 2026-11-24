"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource, workflow

# 专用领域路由先注册，固定命名空间优先于通用 /api/{module} 动态路径。
ROUTERS = [workflow, resource]
