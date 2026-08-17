"""
MonConfig 模块配置加载器
只加载最近的模块 .monconfig，并识别 Mon 双锚点工作区
"""

from pathlib import Path
from typing import Optional, Any, Dict, List, Union
from configparser import ConfigParser


class MonConfig:
    """
    Mon 项目 .monconfig 配置加载器

    功能：
    - 从当前目录向上查找最近的 .monconfig 文件
    - 不进行父目录配置继承
    - 支持类型转换（bool, int, float, list）

    示例：
        config = MonConfig()

        port = config.get("server", "PORT", default=8000, cast=int)
        debug = config.get("django", "DEBUG", default=False, cast=bool)
        hosts = config.get("django", "ALLOWED_HOSTS", default=[], cast=list)
    """

    CONFIG_FILENAME = ".monconfig"
    WORKSPACE_FILENAME = ".monworkspace"

    def __init__(self, start_path: Optional[Union[str, Path]] = None):
        self.start_path = Path(start_path).resolve() if start_path else Path.cwd().resolve()
        self._config = ConfigParser(interpolation=None)
        self._loaded_files: List[Path] = []
        self._workspace_root: Optional[Path] = None
        self._mon_root: Optional[Path] = None
        self._load_configs()

    def _find_config_files(self) -> List[Path]:
        """从当前目录向上查找 .monconfig 文件，返回从远到近排序的路径列表"""
        config_files = []
        current_path = self.start_path
        if current_path.is_file():
            current_path = current_path.parent

        while True:
            config_file = current_path / self.CONFIG_FILENAME
            if self._mon_root is None and config_file.is_file() and (current_path / self.WORKSPACE_FILENAME).is_file():
                self._mon_root = current_path
            if not config_files and config_file.is_file():
                config_files.append(config_file)
                self._workspace_root = current_path

            parent = current_path.parent
            if parent == current_path:
                break
            current_path = parent

        if self._mon_root is None:
            self._mon_root = self._workspace_root
        return config_files

    def _load_configs(self):
        """加载所有找到的配置文件"""
        config_files = self._find_config_files()

        if not config_files:
            raise FileNotFoundError(
                f"未找到 .monconfig 配置文件 (从 {self.start_path} 向上查找)"
            )

        for config_file in config_files:
            normalized_lines = []
            for line_number, raw_line in enumerate(config_file.read_text(encoding="utf-8").splitlines(), start=1):
                line = self._strip_inline_comment(raw_line).strip()
                if not line:
                    continue
                if line.startswith("["):
                    if not line.endswith("]") or not line[1:-1].strip():
                        raise ValueError(f"{config_file}:{line_number}: 无效的配置节")
                    normalized_lines.append(f"[{line[1:-1].strip().lower()}]")
                elif "=" in line and line.split("=", 1)[0].strip():
                    normalized_lines.append(line)
                else:
                    raise ValueError(f"{config_file}:{line_number}: 配置项必须使用 KEY=VALUE")
            self._config.read_string("\n".join(normalized_lines), source=str(config_file))
            self._loaded_files.append(config_file)

    def get(
        self,
        section: str,
        key: str,
        default: Any = None,
        cast: Optional[type] = None,
    ) -> Any:
        """获取配置值，支持类型转换"""
        try:
            value = self._config.get(section, key)
        except Exception:
            return default

        value = self._clean_value(value)
        if not value:
            return default

        if cast is not None:
            return self._cast_value(value, cast)
        return value

    def section(self, section: str) -> Dict[str, str]:
        """获取整个配置节"""
        if not self._config.has_section(section):
            return {}
        return dict(self._config.items(section))

    def sections(self) -> List[str]:
        """获取所有配置节名称"""
        return self._config.sections()

    def workspace_root(self) -> Optional[Path]:
        """第一个找到的 .monconfig 所在目录"""
        return self._workspace_root

    def module_root(self) -> Optional[Path]:
        return self._workspace_root

    def mon_root(self) -> Optional[Path]:
        return self._mon_root

    def loaded_files(self) -> List[Path]:
        return self._loaded_files.copy()

    @staticmethod
    def _clean_value(value: str) -> str:
        return value.strip()

    @staticmethod
    def _strip_inline_comment(line: str) -> str:
        in_single_quote = False
        in_double_quote = False
        for index, character in enumerate(line):
            if character == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
            elif character == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
            elif character in "#;" and not in_single_quote and not in_double_quote:
                if index == 0 or line[index - 1].isspace():
                    return line[:index]
        return line

    @staticmethod
    def _cast_value(value: str, cast_type: type) -> Any:
        if cast_type == bool:
            v = value.strip().lower()
            if v in ("true", "yes", "1", "on"):
                return True
            if v in ("false", "no", "0", "off"):
                return False
            raise ValueError(f"无效的布尔值: '{value}'")
        if cast_type == list:
            return [item.strip() for item in value.split(",") if item.strip()]
        return cast_type(value)
