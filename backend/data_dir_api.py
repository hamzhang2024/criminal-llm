"""
数据目录管理 API

允许用户查看、更改和迁移数据存储位置。
"""
import json
import shutil
import sys
from pathlib import Path

from _bootstrap import DATA_DIR as CURRENT_DATA_DIR
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/data-dir", tags=["数据目录管理"])

# 配置文件存储旧数据目录信息（用于迁移）
DATA_DIR_CONFIG_FILE = CURRENT_DATA_DIR / "data-dir-config.json"

# 默认数据目录（平台相关）
if sys.platform == "darwin":
    DEFAULT_DATA_DIR = Path.home() / "Documents" / ".criminal-llm-data"
elif sys.platform == "win32":
    if getattr(sys, "frozen", False):
        DEFAULT_DATA_DIR = Path(sys.executable).resolve().parent.parent / "data"
    else:
        DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
else:
    DEFAULT_DATA_DIR = Path.home() / ".criminal-llm-data"

# 禁止作为数据目录的系统敏感路径前缀（绝对路径比较，resolve 后校验）
FORBIDDEN_DIR_PREFIXES = []
_home = Path.home()
for p in [
    Path("/etc"), Path("/usr"), Path("/System"), Path("/Library"),
    Path("/bin"), Path("/sbin"), Path("/var"),
    Path("/private/etc"), Path("/private/var"),
    _home / ".ssh",
    _home / "Library" / "LaunchAgents",
    _home / "Library" / "LaunchDaemons",
    _home / "Library" / "Keychains",
]:
    FORBIDDEN_DIR_PREFIXES.append(str(p))

# Windows 敏感路径
if sys.platform == "win32":
    for env in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)"):
        val = __import__("os").environ.get(env)
        if val:
            FORBIDDEN_DIR_PREFIXES.append(str(Path(val)))


def _validate_data_dir(new_path: Path) -> str | None:
    """校验数据目录是否安全，返回错误信息或 None。"""
    try:
        resolved = new_path.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        return f"路径解析失败: {e}"

    resolved_str = str(resolved)
    for forbidden in FORBIDDEN_DIR_PREFIXES:
        if resolved_str == forbidden or resolved_str.startswith(forbidden.rstrip("/") + "/"):
            return f"不允许使用系统敏感目录: {resolved_str}"

    # 必须位于用户主目录或程序自带 data 目录下，避免任意位置写入
    allowed_roots = [str(_home), str(Path(__file__).resolve().parent.parent / "data")]
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        allowed_roots.append(str(Path(sys.executable).resolve().parent.parent / "data"))
    if not any(resolved_str == r or resolved_str.startswith(r.rstrip("/") + "/") for r in allowed_roots):
        return f"数据目录必须位于用户主目录下: {resolved_str}"

    return None



def _load_data_dir_config() -> dict:
    """加载数据目录配置"""
    if DATA_DIR_CONFIG_FILE.exists():
        try:
            with open(DATA_DIR_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_data_dir_config(config: dict):
    """保存数据目录配置"""
    DATA_DIR_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _get_active_data_dir() -> Path:
    """获取当前激活的数据目录"""
    config = _load_data_dir_config()
    custom = config.get("custom_dir")
    if custom:
        return Path(custom)
    return CURRENT_DATA_DIR


@router.get("")
async def get_data_dir():
    """获取当前数据目录信息"""
    active_dir = _get_active_data_dir()
    config_file = str(CURRENT_DATA_DIR / "criminal-llm-config.json")

    return {
        "current_dir": str(active_dir),
        "config_file": config_file,
        "exists": active_dir.exists(),
        "is_default": str(active_dir) == str(DEFAULT_DATA_DIR),
    }


class SetDataDirRequest(BaseModel):
    new_dir: str


@router.post("")
async def set_data_dir(request: SetDataDirRequest):
    """设置新的数据目录"""
    new_path = Path(request.new_dir)

    # 安全校验：禁止系统敏感目录、限制可写位置
    err = _validate_data_dir(new_path)
    if err:
        return {"success": False, "error": err}

    if not new_path.exists():
        return {"success": False, "error": f"目录不存在: {request.new_dir}"}

    if not new_path.is_dir():
        return {"success": False, "error": "路径不是目录"}

    # 保存自定义目录配置
    _save_data_dir_config({"custom_dir": str(new_path.resolve())})

    return {
        "success": True,
        "message": f"数据目录已更改为 {new_path.resolve()}",
        "new_dir": str(new_path.resolve()),
    }


@router.post("/migrate")
async def migrate_data():
    """从旧版本数据目录迁移到当前数据目录"""
    active_dir = _get_active_data_dir()

    # 确定旧数据目录（开发模式下的 data/ 文件夹）
    if getattr(sys, "frozen", False):
        # 打包模式：data/ 在安装目录下
        old_dir = Path(sys.executable).resolve().parent.parent / "data"
    else:
        # 开发模式：data/ 在项目 backend/../data
        old_dir = Path(__file__).resolve().parent.parent / "data"

    if str(old_dir) == str(active_dir):
        return {"success": False, "error": "当前已在最新数据目录，无需迁移"}

    if not old_dir.exists():
        return {"success": False, "error": f"旧数据目录不存在: {old_dir}"}

    # 迁移数据：复制 cases 和 cache 目录
    migrated = []
    errors = []

    for subdir in ["cases", "cache"]:
        src = old_dir / subdir
        dst = active_dir / subdir
        if src.exists():
            try:
                dst.mkdir(parents=True, exist_ok=True)
                # 逐个复制，跳过已存在的文件
                for item in src.iterdir():
                    dst_item = dst / item.name
                    if not dst_item.exists():
                        if item.is_dir():
                            shutil.copytree(str(item), str(dst_item))
                        else:
                            shutil.copy2(str(item), str(dst_item))
                        migrated.append(item.name)
            except Exception as e:
                errors.append(f"{subdir}: {str(e)}")

    if migrated and not errors:
        message = f"成功迁移 {len(migrated)} 个项目: {', '.join(migrated)}"
    elif migrated:
        message = f"部分迁移成功: {', '.join(migrated)}。失败: {', '.join(errors)}"
    else:
        return {"success": False, "error": f"迁移失败: {', '.join(errors)}"}

    return {
        "success": True,
        "message": message,
        "migrated": migrated,
    }
