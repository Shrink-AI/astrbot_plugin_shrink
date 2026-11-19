"""Conservative controls for explicit stop requests and unverified action claims."""
import re


def is_stop_request(text):
    text = re.sub(r"[\s，,。.!！]+", "", text or "")
    return bool(re.fullmatch(
        r"(?:石英)?(?:你)?(?:先)?(?:停下来|停下|停止|暂停|别干了|别做了|取消任务)"
        r"(?:(?:先)?(?:停下来|停止|暂停|别干了|别做了|不要继续|不用继续))*", text))


def is_revocation(text):
    return bool(re.fullmatch(r"\s*(?:石英[，, ]*)?(?:撤销|撤回|取消)(?:之前的|所有)?授权[。！!\s]*", text or ""))


def unsupported_claim(text, steps, task=None):
    from .task_reporting import desktop_requested
    text = text or ""
    desktop = (task or {}).get("desktop_execution")
    if desktop or desktop_requested(text):
        for clause in re.split(r"[，,。！？!?\n；;]", text):
            if re.search(r"未|没有|没|不能|不代表|无法|若|如果|还在等|等待|查到|失败", clause):
                continue
            match = re.search(r"开跑|启动|(?:已经|正在|还在|已)(?:\s*在)?(?:电脑|Windows|后台)?.*?"
                              r"(?:执行|跑|运行|完成|启动)|后台.*?跑|跑完|做完|执行完|任务已完成", clause, re.I)
            if match:
                allowed = ({"completed"} if re.search(r"完成|跑完|做完|执行完", match.group())
                           else {"running"} if re.search(r"正在|还在|运行|后台.*跑", match.group(), re.I)
                           else {"running", "completed"})
                receipts = [step.get("execution") or {} for step in steps or []]
                if not ((desktop or {}).get("status") in allowed or any(
                        item.get("domain") == "desktop" and item.get("status") in allowed for item in receipts)):
                    return True
    claim = re.search(
        r"(?:同步|提交|推送|部署|安装|修改|发送|关闭|取消)(?:好|完|成功|完成)了?"
        r"|(?:正在|已经|已)(?:帮你|给你)?(?:推送|提交|同步|部署|执行|处理|跑|推)"
        r"|后台(?:还在|正在)?跑|任务(?:交给|交到|提交|提上|交出)",
        text)
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


def is_task_followup(text, decision=None):
    if decision and not decision["fallback"] and decision.get("reason") != "decision_disabled":
        return bool(decision["decision"]["task"] or decision["decision"]["intent"] == "supplement"
                    or re.search(r"处理得怎么样|做到哪|还在跑|还在运行", text or ""))
    return bool(re.search(r"进度|干完|做完|处理完|处理得怎么样|做到哪|还在跑|还在运行|同步|提交|推送|部署|停下来|别干了",
                          text or ""))
