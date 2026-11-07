export const date = (value: number | null | undefined) =>
  value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—';

export const shortDate = (value: number | null | undefined) =>
  value ? new Date(value * 1000).toLocaleDateString('zh-CN') : '—';

export const place = (source: string) =>
  !source ? '—' : source.includes(':private:') ? '私聊' : source === 'admin' ? '管理员录入' : source.split(':').pop() + ' 群';

export const runLabels: Record<string, string> = {
  success: '完成', failed: '失败', running: '进行中', interrupted: '中断',
};
export const runKinds: Record<string, string> = {
  digest: '整理群聊', consolidate: '夜间整理', teach: '定向学习', glossary: '黑话', skill: '经验复盘',
};
export const syncLabels: Record<string, string> = {
  synced: '已同步', not_attempted: '未尝试', restored: '失败后已恢复',
  recovery_required: '需要恢复', failed: '失败', pending: '同步中',
};
export const memoryKinds: Record<string, string> = {
  impression: '印象', event: '事件', topic: '话题', glossary: '黑话', lesson: '提醒',
};
export const memoryStatus: Record<string, string> = {
  active: '正式', candidate: '候选', superseded: '已取代', rejected: '错误', expired: '已过期',
};
export const certainty: Record<string, string> = {
  stated: '本人说的', inferred: '推断', hearsay: '转述', joke: '玩笑',
};
export const origins: Record<string, string> = {
  digest: '群聊整理', explicit: '群友要求记住', curated: '人工整理',
  admin_teach: '管理员', glossary: '黑话查询', ai: 'AI 推断', admin: '管理员', seed: '预置',
};
export const reviewKinds: Record<string, string> = {
  nickname_conflict: '称呼冲突', memory_conflict: '记忆矛盾', memory_candidate: '候选记忆',
  glossary_guess: '推测的黑话', nickname_guess: '推断的称呼', profile_new: '新人物小结',
  suspected_bot: '疑似机器人', memory_unverified: '未确认记忆',
};
export const rejectReasons: Record<string, string> = {
  evidence: '依据编号无效', quote_not_found: '引用对不上原文', bot_only_evidence: '只有机器人的话作依据',
  joke: '玩笑', certainty: '确定程度无效', subject: '对象无效', attribution: '对象和依据对不上',
  bot_self_report: '机器人自述', length: '长度不合适', self_identity: '涉及石英身份',
  relative_time: '相对时间', private: '隐私', insult: '贬低', importance: '重要度无效',
  low_importance: '闲聊水话', participants: '参与者无效', person: '人无效', name: '称呼无效',
  name_not_in_quote: '称呼不在引用里', already_known: '已知称呼', term: '词无效',
  person_name: '是人名', not_to_bot: '不是对石英说的', format: '格式错误', kind: '类型错误',
  digest_limit: '超过单次上限', subject_limit: '同一人超过上限', daily_limit: '超过每日上限',
  claim: '核对未通过', nickname: '核对未通过',
};

export function reasonLabel(key: string) {
  const [group, reason] = key.includes(':') ? key.split(':') : ['', key];
  const groups: Record<string, string> = {
    memories: '记忆', topics: '话题', nicknames: '称呼', jargon: '黑话', lessons: '提醒', verify: '二次核对', limit: '限流',
  };
  return `${groups[group] ?? group}·${rejectReasons[reason] ?? reason}`;
}

export function aliases(raw: string) {
  try { return (JSON.parse(raw) as string[]).filter(Boolean); } catch { return []; }
}
