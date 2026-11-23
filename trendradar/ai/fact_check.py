# coding=utf-8
"""
AI 输出事实校验

提示词只能"软约束"模型不许编造数字。本模块在代码层做硬校验：
把 AI 输出的每一个关键数字，回查原始素材文本，凡是素材里查不到的数字，
按板块规则处理（指标表删除、精选与研判行标记〔待核〕），并在日志里报出统计。

设计原则：
- 只校验"关键数值"：带小数点的、4 位以上的、带百分号的数字。
  「12 条」「未来 1-2 周」「11-28」这类结构与日期片段（纯整数且位数少）不参与校验，
  否则会把正常表述全部误判。
- 千分位（4,052）与空格（4 052）在比对前统一归一化。
- 素材文本必须包含该数字的原始写法，才视为"有据"。
"""

import re
from typing import Any, List, Set, Tuple

STRUCT_SEP = "｜"
PENDING_MARK = "〔待核〕"
PENDING_VALUE_MARK = "〔现值待核〕"

# 数字片段：支持千分位与小数
_NUM_TOKEN = re.compile(r"(\d[\d,]*(?:\.\d+)?)")
# 需要参与校验的数字特征
_PCT_AFTER = re.compile(r"^\s*[%％]")


def _normalize(text: str) -> str:
    """归一化：去掉千分位逗号与各种空白，便于子串比对。"""
    return re.sub(r"[,，、\s]", "", text or "")


def _tokens_in_source(source_text: str) -> str:
    """返回归一化后的素材文本（作为"可查证"的基准）。"""
    return _normalize(source_text)


def _checkable_numbers(line: str) -> List[str]:
    """从一行文本中取出需要回查的关键数字。"""
    out: List[str] = []
    for m in _NUM_TOKEN.finditer(line or ""):
        raw = m.group(1)
        num = raw.replace(",", "")
        tail = line[m.end(): m.end() + 1]
        has_pct = bool(_PCT_AFTER.match(tail))
        needs = ("." in num) or (len(num) >= 4) or has_pct
        if needs and num not in out:
            out.append(num)
    return out


def _unverified_numbers(line: str, normalized_source: str) -> List[str]:
    return [n for n in _checkable_numbers(line) if n not in normalized_source]


def verify_analysis_result(
    result: Any,
    source_text: str,
    debug: bool = True,
) -> Tuple[Any, dict]:
    """按板块规则校验并修正 AI 分析结果。

    Args:
        result: 含 core_trends / signals / outlook_strategy 等字段的分析结果对象
        source_text: 送入 AI 的原始素材文本（热榜 + RSS + 联网搜索 + 独立展示区）
        debug: 是否打印校验统计

    Returns:
        (修正后的 result, 统计字典)
    """
    stats = {
        "checked": 0,
        "unverified": 0,
        "rows_dropped": 0,
        "rows_marked": 0,
        "rows_shaped_out": 0,
    }

    if not source_text:
        if debug:
            print("[校验] 无素材文本，跳过事实校验")
        return result, stats

    src = _tokens_in_source(source_text)

    # ── 1. 关键指标表：无据的行直接删除（表必须是可查证的数值）；
    #      若删完不足 4 行，则改为标记保留，避免整表消失。
    signals = getattr(result, "signals", "") or ""
    if signals:
        keep: List[str] = []
        dropped: List[Tuple[str, List[str]]] = []
        for raw_line in signals.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if STRUCT_SEP not in line:
                keep.append(line)
                stats["rows_shaped_out"] += 1
                continue
            bad = _unverified_numbers(line, src)
            stats["checked"] += len(_checkable_numbers(line))
            if bad:
                stats["unverified"] += len(bad)
                dropped.append((line, bad))
            else:
                keep.append(line)
        if dropped and len(keep) < 4:
            for line, _bad in dropped:
                keep.append(f"{line}{PENDING_VALUE_MARK}")
                stats["rows_marked"] += 1
        else:
            stats["rows_dropped"] += len(dropped)
        setattr(result, "signals", "\n".join(keep))

    # ── 2. 精选条目：保留信息，但对含无据数字的行标注〔待核〕
    for field in ("core_trends", "outlook_strategy", "search_insights", "rss_insights"):
        text = getattr(result, field, "") or ""
        if not text:
            continue
        out_lines: List[str] = []
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            bad = _unverified_numbers(line, src)
            stats["checked"] += len(_checkable_numbers(line))
            if bad:
                stats["unverified"] += len(bad)
                if PENDING_MARK not in line:
                    line = f"{line}{PENDING_MARK}"
                    stats["rows_marked"] += 1
            out_lines.append(line)
        setattr(result, field, "\n".join(out_lines))

    if debug:
        print(
            "[校验] 数字回查：检验 {c} 个，无据 {u} 个；删除无据指标行 {d} 行，"
            "标注待核 {m} 行，结构不符剔除 {s} 行".format(
                c=stats["checked"],
                u=stats["unverified"],
                d=stats["rows_dropped"],
                m=stats["rows_marked"],
                s=stats["rows_shaped_out"],
            )
        )

    return result, stats


def build_source_text(*parts: str) -> str:
    """把各来源素材拼成用于回查的基准文本。"""
    return "\n".join(p for p in parts if p)


def count_selected_items(core_trends: str) -> Set[str]:
    """统计精选板块的条目编号（用于日志核对是否达到目标条数）。"""
    return set(re.findall(r"^(\d+)\.", core_trends or "", re.M))
