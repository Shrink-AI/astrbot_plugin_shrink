"""Pure text rules shared by persistence (ingest gate) and services (quality gates)."""

import re

COMMAND = re.compile(r"^\s*[/!！]")
INJECTION = re.compile(
    r"忽略.{0,12}(指令|规则|提示)|"
    r"(修改|覆盖|替换|输出|泄露).{0,12}(系统提示|管理员密码|工具权限)|"
    r"(执行|运行).{0,8}(命令|脚本)|关闭.{0,8}(采集|学习)|"
    r"(ignore|disregard).{0,20}(instruction|rule|prompt)",
    re.I,
)
URL_ONLY = re.compile(r"^\s*(https?://\S+\s*)+$", re.I)
LONG_TEXT = 500
GENERIC_ADDRESS = {
    "大家", "你们", "群友", "各位", "宝子们", "兄弟们", "姐妹们", "家人们", "群主", "管理",
    "老师们", "朋友们", "大佬们", "所有人", "全体成员", "机器人", "bot", "你", "我", "他", "她",
}
SELF_IDENTITY = re.compile(
    r"石英.{0,6}(就是|是|叫|名叫|改名|的(妹妹|姐姐|哥哥|弟弟|爸|妈|爹|娘|家人|老婆|老公))"
)
RELATIVE_DAY = re.compile(r"今天|明天|昨天|后天|前天|今晚|明早|这周|下周|上周|本周|这个月|下个月|上个月")
INSULT = re.compile(r"傻逼|sb\b|煞笔|脑残|弱智|滚|废物|垃圾|去死|nmsl|cnm", re.I)


def learnable(text, *, sender_is_bot=False, is_self=False):
    """Whether a human message may be used to learn style, expressions or facts."""
    text = text or ""
    if is_self or sender_is_bot or not text.strip():
        return False
    if COMMAND.search(text) or INJECTION.search(text) or URL_ONLY.match(text):
        return False
    return len(text) <= LONG_TEXT


def normalize(text):
    return re.sub(r"\s+", "", text or "").lower()


def grams(text, n=3, limit=40):
    """Overlapping character n-grams for CJK runs plus ASCII words; used for lexical overlap."""
    out = []
    for run in re.findall(r"[一-鿿]+|[A-Za-z0-9_]{3,}", text or ""):
        if re.match(r"[A-Za-z0-9_]", run):
            out.append(run.lower())
        elif len(run) < n:
            out.append(run)
        else:
            out.extend(run[i : i + n] for i in range(len(run) - n + 1))
    seen = list(dict.fromkeys(out))
    return seen[:limit]
