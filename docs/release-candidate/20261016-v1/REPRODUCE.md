# RC 复现命令

源码验收基准 `974bd0898324893e5469f3bb60ee1de40adc03ed`。最终文档归档后的 main SHA 用 `git rev-parse main` 读取。

## 应用

```bash
git clone https://github.com/chen0206a/CampusBridge-Agent.git
cd CampusBridge-Agent
docker compose up --build -d
docker compose ps --all
```

本轮未推送；远程克隆在确认发布后获取新版本。本地实际验收使用 `git clone --no-local --branch release/candidate-20261016 <local-repository> <new-directory>`。默认无需 `.env` 或 API Key，访问 http://127.0.0.1:8080/，端口可通过 `CAMPUSBRIDGE_WEB_PORT` 覆盖。普通 `docker compose down` 保留数据卷。

## 新环境测试

```bash
python -m venv .venv
# 激活新环境：Windows .venv/Scripts，Linux .venv/bin
python -m pip install -r requirements.lock
python -m pip install -e . --no-deps
python -m pip check
python scripts/showcase_demo.py --output runs/rc-demo
python scripts/http_smoke.py --output runs/rc-http.json
python -m pip install -r requirements-mysql.lock -r requirements-redis.lock
python -m pytest -q
python scripts/verify_archive.py
python scripts/release_candidate.py --output runs/rc-source-audit.json
python scripts/release.py build --root . --output /new/external/public-source
```

真实 Redis 测试需要本机 Redis，或将 `CAMPUSBRIDGE_TEST_REDIS_COMMAND` 配置为 `[python, scripts/redis_docker_test_server.py]` 的绝对路径 JSON argv，`CAMPUSBRIDGE_TEST_DOCKER` 指向 Docker。夹具只操作自己的临时 Redis。

```bash
python scripts/test_mysql_d1.py --mysqld /installed/mysql/bin/mysqld --scratch /new/ascii-scratch --output /new/mysql-results --extra-test tests/test_redis_mysql_d2.py --extra-test tests/test_mysql_d5.py
python scripts/compose_acceptance.py --root . --project cb-d3-rc-new --port 8085 --output /new/compose-results
python scripts/compose_redis_probe.py --root . --project cb-d3-rc-new --port 8085 --output /new/redis-probe.json
```

两条 Compose 命令使用同一 project/port/image tag；测试脚本只操作专用新项目。默认 Mock，测试显式清空模型 key。Linux 已安装包验证将完整克隆只读挂载到 `/verify`，工作目录 `/verify`，为 `/tmp` 配置可写 tmpfs，设置 `PYTHONPYCACHEPREFIX=/tmp/pycache`，执行：

```bash
python -m pytest -o pythonpath= -q -p no:cacheprovider tests/test_runtime.py tests/test_api.py tests/test_stage5.py tests/test_stage6.py tests/test_web_api.py tests/test_audit_d5.py tests/test_performance_d5.py
```

从镜像 site-packages 导入应用，保留 eval/src 供固定数据和源码 Hash 测试读取。

```bash
cd frontend
npm ci
# 共享缓存无权限时：npm ci --cache ./tmp/npm-cache
npm test
npx tsc -b --pretty false
npm run build
```

本轮全部 Mock，0 外部模型调用。真实模型与压测表引用已归档历史，无新增实验。
