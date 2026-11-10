"""Conservative controls for explicit stop requests and unverified action claims."""
import re


def is_stop_request(text):
    text = re.sub(r"[\s，,。.!！]+", "", text or "")
    return bool(re.fullmatch(
        r"(?:石英)?(?:你)?(?:先)?(?:停下来|停下|停止|暂停|别干了|别做了|取消任务)"
        r"(?:(?:先)?(?:停下来|停止|暂停|别干了|别做了|不要继续|不用继续))*", text))


def unsupported_claim(text, steps, task=None):
    claim = re.search(
        r"(?:同步|提交|推送|部署|安装|修改|发送|关闭|取消)(?:好|完|成功|完成)了?"
        r"|(?:正在|已经|已)(?:帮你|给你)?(?:推送|提交|同步|部署|执行|处理|跑|推)"
        r"|后台(?:还在|正在)?跑|任务(?:交给|交到|提交|提上|交出)",
        text or "")
    if not claim:
        return False
    prefix = (text or "")[max(0, claim.start() - 10):claim.start()]
    if re.search(r"没有|没|不能|尚未|未确认|不代表", prefix):
        return False
    complete = bool(re.search(r"好了|完了|成功|完成|已经|已", claim.group()))
    allowed = {"completed"} if complete else {"submitted", "queued", "running", "waiting", "completed"}
    if task and task.get("status") in allowed:
        return False
    return not any((step.get("execution") or {}).get("status") in allowed for step in steps or [])


def is_task_followup(text):
    return bool(re.search(r"进度|干完|做完|处理完|同步|提交|推送|部署|停下来|别干了", text or ""))
