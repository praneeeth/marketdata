"""Deterministic rules for advice-like content (research_only mode).

Every rule runs against :func:`normalize_for_detection` output, i.e. case-folded text
with obfuscation removed. Rules are intentionally conservative: a false positive costs
one redacted sentence, a false negative can put unregistered investment advice in front
of the public.

Descriptive technical facts (support/resistance, moving averages, 52-week range,
historical moves, portfolio composition) are not matched by any rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    DIRECTIVE = "directive"
    RATING = "rating"
    PRICE_TARGET = "price_target"
    STOP_LOSS = "stop_loss"
    ENTRY_LEVEL = "entry_level"
    POSITION_SIZE = "position_size"
    TIMING = "timing"
    RETURN_PROMISE = "return_promise"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: Category
    pattern: re.Pattern[str]
    # Only match at the start of a sentence (imperatives such as "Buy on dips.").
    sentence_start: bool = False
    # If this pattern matches the 60 characters before the hit, the hit is discarded.
    veto_before: re.Pattern[str] | None = None


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_CUR = r"(?:₹|rs\.?|inr|rupees?|\$|usd)"
_NUM = r"\d[\d,]*(?:\.\d+)?"
# A number that looks like a share/index price rather than a percentage or an amount.
_PRICE_NUM = (
    rf"(?:{_CUR}\s*)?{_NUM}"
    r"(?!\s*(?:%|percent|bps|basis|crores?|cr\b|lakhs?|bn\b|billion|mn\b|million|trillion"
    r"|tn\b|tonnes?|mw\b|gw\b|units|customers|stores|outlets|users|subscribers|km\b))"
)
_ACTION_VERBS = (
    r"(?:buy|buying|sell|selling|hold|holding|accumulate|accumulating|add|adding|exit|exiting"
    r"|book|booking|trim|trimming|reduce|reducing|increase|increasing|short|shorting"
    r"|go\s+long|go\s+short|enter|entering|invest|investing|purchase|purchasing|average"
    r"|averaging|switch|switching|avoid|avoiding|stay\s+away|keep\s+holding|square\s+off"
    r"|stay\s+invested|take\s+(?:a\s+)?position)"
)
_CORPORATE_TARGET_CONTEXT = _rx(
    r"\b(?:revenue|sales|profit|earnings|ebitda|margins?|orders?|order\s+book|production"
    r"|capacity|volumes?|fund[\s-]?rais(?:e|ing)|capex|growth|debt|deleverag\w*|disinvestment"
    r"|divestment|fiscal|deficit|inflation|exports?|launch(?:es)?|stores?|users?|subscribers?"
    r"|deliver(?:y|ies)|aum|loans?|deposits?|emissions?|net[\s-]zero|hiring|headcount"
    r"|guidance|company|management|government|ministry|rbi)\b"
)
_CORPORATE_ACTOR = _rx(
    r"\b(?:company|companies|firm|group|board|management|government|govt|centre|center"
    r"|ministry|fund\s+house|amc|lic|promoters?|rbi|sebi|conglomerate|subsidiary|insurer)\b"
)


RULES: tuple[Rule, ...] = (
    # ---------------------------------------------------------------- directives
    Rule(
        "D01_you_should_act",
        Category.DIRECTIVE,
        _rx(
            r"\byou\s+(?:should|must|could|can|may|might|need\s+to|ought\s+to|would\s+do\s+well\s+to"
            r"|may\s+want\s+to|might\s+want\s+to|would\s+want\s+to)\s+"
            r"(?:(?:also|definitely|probably|consider|think\s+about|look\s+to|start|try\s+to"
            r"|continue\s+to)\s+)*" + _ACTION_VERBS + r"\b"
        ),
    ),
    Rule(
        "D02_we_recommend",
        Category.DIRECTIVE,
        _rx(r"\b(?:i|we)\s+(?:would\s+)?(?:strongly\s+)?(?:recommend|suggest|advise|advice)\b"),
    ),
    Rule(
        "D03_advisable_to_act",
        Category.DIRECTIVE,
        _rx(
            r"\b(?:recommend(?:ed|s|ing)?|advis(?:e|ed|able|ing)|suggest(?:ed|s|ing)?|better|wise|prudent)"
            r"\s+(?:to\s+)?(?:buy|sell|hold|accumulate|add|exit|book|trim|reduce|short|enter|invest"
            r"|avoid|stay\s+away)\b"
        ),
    ),
    Rule(
        "D04_consider_acting",
        Category.DIRECTIVE,
        _rx(
            r"\bconsider\s+(?:buying|selling|accumulating|adding|exiting|booking|trimming|reducing"
            r"|increasing|shorting|holding|entering|averaging|investing\s+in|switching|going\s+long"
            r"|going\s+short)\b"
        ),
    ),
    Rule(
        "D05_imperative",
        Category.DIRECTIVE,
        _rx(
            r"^[\W\d]*(?:buy(?![\s-]*(?:side|back))|sell(?![\s-]*(?:off|side))|accumulate|trim"
            r"|exit(?!\s+(?:polls?|load))|short\s+(?:the|this|it|on|at|above|below|near|around|if)\b"
            r"|add\s+(?:more|on|at|to|near|around|below|above)\b"
            r"|hold(?:\s*[.!]|\s*$|\s+(?:on|for|with|the|your|this|it|tight|firm)\b)"
            r"|book\s+(?:partial\s+)?profits?|go\s+(?:long|short)|enter\b|avoid\b"
            r"|stay\s+(?:away|invested)|average\s+(?:down|out)|square\s+off"
            r"|(?:reduce|trim|cut|lighten|increase|raise|scale\s+(?:up|down))\s+"
            r"(?:exposure|positions?|allocations?|holdings?|weight(?:age)?))"
        ),
        sentence_start=True,
    ),
    Rule(
        "D06_act_at_level",
        Category.DIRECTIVE,
        _rx(
            r"\b(?:buy|buying|sell|selling|accumulate|add|exit|short|enter|initiate)\s+"
            r"(?:(?:it|the\s+stock|the\s+shares|this|more|fresh)\s+)?"
            r"(?:on\s+(?:dips|declines|rallies|strength|weakness|every\s+dip|any\s+dip"
            r"|a\s+(?:pullback|breakout|dip|correction))"
            r"|at\s+(?:current|cmp|market|lower|higher)?\s*(?:levels?|prices?)?\s*"
            + _PRICE_NUM
            + r"|(?:around|near|below|above|between|in\s+the\s+range|upto|up\s+to|till"
            r"|if\s+it\s+(?:breaks|falls|rises|crosses|dips)|once\s+it)\b)"
        ),
    ),
    Rule(
        "D07_should_be_bought",
        Category.DIRECTIVE,
        _rx(
            r"\b(?:should|must|can|could|may)\s+be\s+(?:bought|sold|accumulated|exited|avoided|added|held)\b"
        ),
    ),
    Rule(
        "D08_continue_holding",
        Category.DIRECTIVE,
        _rx(
            r"\b(?:continue\s+to\s+hold|keep\s+holding|hold\s+on\s+to|hold\s+for\s+(?:the\s+)?"
            r"(?:long|medium|short)[\s-]term|remain\s+invested|stay\s+invested)\b"
        ),
    ),
    Rule(
        "D09_investors_should_act",
        Category.DIRECTIVE,
        _rx(
            r"\b(?:investors|traders|holders|long[\s-]term\s+investors|short[\s-]term\s+traders|one"
            r"|readers|users|existing\s+holders|new\s+investors|shareholders)\s+"
            r"(?:can|could|should|may|might|must|are\s+advised\s+to|would\s+do\s+well\s+to)\s+"
            r"(?:(?:consider|look\s+to|continue\s+to|think\s+about)\s+)?" + _ACTION_VERBS + r"\b"
        ),
    ),
    Rule(
        "D10_your_exposure",
        Category.DIRECTIVE,
        _rx(
            r"\b(?:increase|reduce|cut|trim|raise|lower|build|scale\s+(?:up|down)|square\s+off"
            r"|lighten|exit|rebalance)\s+(?:your|one's)\s+(?:exposure|position|positions|allocation"
            r"|holding|holdings|stake|weight|weightage|investment|portfolio)\b"
        ),
    ),
    Rule(
        "D11_avoid_stock",
        Category.DIRECTIVE,
        _rx(
            r"\b(?:avoid|stay\s+away\s+from)\s+(?:the|this|these|such)\s+(?:stock|stocks|share|shares"
            r"|scrip|counter|name|names)\b"
        ),
    ),
    Rule(
        "D12_hinglish",
        Category.DIRECTIVE,
        _rx(
            r"\b(?:khareed(?:o|en|ein|lo|na|iye)?|kharid(?:o|en|ein|lo|na|iye)?|bech(?:o|en|ein|do|na|iye)"
            r"|le\s+lo|nikal\s+(?:jao|lo)|hold\s+karo|profit\s+book\s+karo)\b"
        ),
    ),
    Rule(
        "D13_chinese",
        Category.DIRECTIVE,
        _rx(
            r"(?:买入|卖出|建仓|加仓|减仓|清仓|止损|止盈|目标价|增持|减持|抄底|逢低(?:买|吸|布局|介入)"
            r"|逢高(?:卖|减|出)|半仓|满仓|轻仓|重仓|仓位|操作建议|最终决策|投资评级|建议(?:买|卖|持有|加|减|关注|回避|规避)"
            r"|可以?(?:买|卖)(?:入|出)?|继续持有|考虑(?:买|卖|加|减|止))"
        ),
    ),
    Rule(
        "D14_advice_framing",
        Category.DIRECTIVE,
        _rx(
            r"\b(?:my|our)\s+(?:recommendation|recommendations|advice|suggestion|call|pick|picks)\b"
            r"|\binvestment\s+(?:advice|recommendation)\s*[:\-]"
            r"|\btrading\s+(?:call|calls|tip|tips|idea|ideas|recommendation|recommendations)\b"
            r"|\bstock\s+tips?\b|\bintraday\s+(?:call|calls|tip|tips|pick|picks)\b|\btop\s+picks?\b"
            r"|\bstocks?\s+to\s+(?:buy|sell|accumulate|avoid)\b"
            r"|\b(?:buy|sell|hold)\s+(?:call|calls|signal|signals|recommendation|recommendations"
            r"|idea|ideas|rating|ratings)\b"
        ),
    ),
    # ------------------------------------------------------------------- ratings
    Rule("R01_strong_buy", Category.RATING, _rx(r"\b(?:strong|outright)\s+(?:buy|sell)\b")),
    Rule(
        "R02_rating_value",
        Category.RATING,
        _rx(
            r"\b(?:rating|rated|stance|recommendation|verdict)\s*(?:of|is|:|-|to|as|at)?\s*(?:a\s+|an\s+)?"
            r"[\"']?(?:buy|sell|hold|accumulate|reduce|neutral|outperform|underperform|overweight"
            r"|underweight|add|market\s*perform|sector\s*perform|equal\s*weight)\b"
        ),
    ),
    Rule(
        "R03_rating_word",
        Category.RATING,
        _rx(
            r"\b(?:outperform|underperform|overweight|underweight|accumulate|equal[\s-]weight"
            r"|market[\s-]perform|sector[\s-]perform)\s+(?:rating|call|recommendation|stance|view)\b"
        ),
    ),
    Rule(
        "R04_rating_change",
        Category.RATING,
        _rx(
            r"\b(?:upgrade[sd]?|downgrade[sd]?|initiate[sd]?|initiating|maintain(?:s|ed)?"
            r"|reiterate[sd]?)\s+(?:\S+\s+){0,3}(?:to\s+|at\s+|with\s+)?(?:a\s+)?[\"']?"
            r"(?:buy|sell|hold|accumulate|reduce|neutral|outperform|underperform|overweight"
            r"|underweight)\b"
        ),
    ),
    # ------------------------------------------------------------- price targets
    Rule(
        "T01_price_target_phrase",
        Category.PRICE_TARGET,
        _rx(r"\b(?:price\s+targets?|target\s+prices?|price\s+objectives?|tp|tgt|sell\s+target)\b"),
    ),
    Rule(
        "T02_target_number",
        Category.PRICE_TARGET,
        _rx(r"\btargets?\s*(?:of|at|is|are|:|-|–|to|around|near|~|@|=)?\s*" + _PRICE_NUM),
        veto_before=_CORPORATE_TARGET_CONTEXT,
    ),
    Rule(
        "T03_upside_to",
        Category.PRICE_TARGET,
        _rx(
            r"\b(?:upside|downside)\s+(?:target|potential|of|to|till|towards)\s+(?:"
            + _CUR
            + r"\s*)?\d"
        ),
    ),
    Rule(
        "T04_percent_upside",
        Category.PRICE_TARGET,
        _rx(
            r"\b\d[\d.]*\s*(?:%|percent)\s+(?:upside|downside|potential\s+upside|return\s+potential)\b"
        ),
    ),
    Rule(
        "T05_predicted_level",
        Category.PRICE_TARGET,
        _rx(
            r"\b(?:could|can|may|might|will|should|is\s+likely\s+to|is\s+expected\s+to|expected\s+to"
            r"|likely\s+to|poised\s+to|set\s+to|headed|heading)\s+(?:rise|rally|climb|move|go|head"
            r"|surge|jump|soar|zoom|touch|reach|hit|test|cross|fall|drop|decline|slide|slip|crash"
            r"|tumble|correct)\s+(?:up\s+|down\s+|back\s+)?(?:to(?:wards)?\s+|till\s+|until\s+"
            r"|up\s+to\s+|upto\s+|near\s+|around\s+|beyond\s+|past\s+|above\s+|below\s+)?(?:the\s+)?"
            + _PRICE_NUM
        ),
        veto_before=_CORPORATE_TARGET_CONTEXT,
    ),
    Rule(
        "T06_fair_value",
        Category.PRICE_TARGET,
        _rx(
            r"\b(?:fair|intrinsic|target)\s+value\s*(?:of|at|is|:|-|around|estimate\s+of)?\s*"
            r"(?:" + _CUR + r"\s*)?\d"
        ),
    ),
    Rule(
        "T07_expect_price",
        Category.PRICE_TARGET,
        _rx(
            r"\b(?:expect|expects|expecting|see|sees|seeing|project|projects|forecast|forecasts"
            r"|estimate|estimates)\s+(?:the\s+)?(?:stock|share|shares|scrip|price|it|nifty|sensex"
            r"|index)\s+(?:to\s+)?(?:at|reach|hit|touch|cross|rise\s+to|fall\s+to|trade\s+at|go\s+to"
            r"|move\s+to)\s+" + _PRICE_NUM
        ),
    ),
    # ---------------------------------------------------------------- stop-loss
    Rule(
        "S01_stop_loss",
        Category.STOP_LOSS,
        _rx(
            r"\bstop[\s\-]*loss(?:es)?\b|\bstoploss\b|\btrailing\s+stop|\bstrict\s+stop\b"
            r"|\bsl\s*(?:[:@\-]|at|of|below|above)?\s*(?:" + _CUR + r"\s*)?\d"
            r"|\bstop\s+(?:at|below|above|near|around)\s+(?:" + _CUR + r"\s*)?\d"
            r"|\bexit\s+(?:if|below|above|on\s+a\s+close\s+(?:below|above))\s+(?:it\s+\w+\s+)?"
            r"(?:" + _CUR + r"\s*)?\d"
        ),
    ),
    # ------------------------------------------------------------- entry levels
    Rule(
        "E01_entry_level",
        Category.ENTRY_LEVEL,
        _rx(
            r"\b(?:entry|entries)\s+(?:(?:point|points|level|levels|zone|zones|price|prices|range)\b"
            r"|(?:at|around|near|between|above|below|:)\s*(?:cmp|current\s+levels?|levels?\s+of)?\s*"
            + _PRICE_NUM
            + r")"
        ),
    ),
    Rule(
        "E02_buy_zone",
        Category.ENTRY_LEVEL,
        _rx(r"\bbuy(?:ing)?\s+(?:zone|range|level|levels|area|price)\b"),
    ),
    Rule(
        "E03_accumulation_zone",
        Category.ENTRY_LEVEL,
        _rx(r"\baccumulat(?:e|ion)\s+(?:zone|range|between|at|around|near|on|level|levels)\b"),
    ),
    Rule(
        "E04_take_position",
        Category.ENTRY_LEVEL,
        _rx(
            r"\b(?:enter|initiate|take)\s+(?:a\s+|fresh\s+|new\s+)?(?:long|short|position|positions"
            r"|trade|trades)\b|\bopen\s+(?:a|fresh|new)\s+(?:long|short|position|trade)\b"
            r"|\bbuy\s+the\s+dip\b"
        ),
    ),
    # ------------------------------------------------------------ position size
    Rule(
        "P01_allocate_percent",
        Category.POSITION_SIZE,
        _rx(
            r"\b(?:allocate|allocating|invest|investing|put|putting|deploy|deploying|park|parking"
            r"|keep|limit|cap|restrict)\s+(?:only\s+|about\s+|around\s+|up\s+to\s+|upto\s+"
            r"|not\s+more\s+than\s+|no\s+more\s+than\s+|at\s+most\s+|a\s+maximum\s+of\s+|max\s+)?"
            r"\d+(?:\.\d+)?\s*(?:%|percent)"
        ),
        veto_before=_CORPORATE_ACTOR,
    ),
    Rule(
        "P02_buy_quantity",
        Category.POSITION_SIZE,
        _rx(
            r"\b(?:buy|purchase|sell|add|accumulate|short)\s+(?:only\s+|about\s+|around\s+)?"
            r"\d[\d,]*\s+(?:shares|share|units|lots?|qty|quantity|stocks)\b"
        ),
    ),
    Rule("P03_lots_of", Category.POSITION_SIZE, _rx(r"\b\d+\s+lots?\s+(?:of|in)\b")),
    Rule(
        "P04_position_sizing",
        Category.POSITION_SIZE,
        _rx(
            r"\bposition[\s\-]+siz(?:e|es|ing)\b|\bhow\s+much\s+to\s+(?:invest|allocate|buy)\b"
            r"|\b(?:half|full|quarter|small|light|heavy|partial)\s+(?:position|positions|allocation"
            r"|quantity|qty)\b|\bportfolio\s+allocation\s+of\s+\d"
        ),
    ),
    # -------------------------------------------------------------------- timing
    Rule(
        "M01_good_time_to_act",
        Category.TIMING,
        _rx(
            r"\b(?:good|right|great|ideal|best|perfect|opportune|attractive|excellent)\s+"
            r"(?:time|moment|opportunity|entry|level|point|price|chance)\s+(?:to|for)\s+"
            r"(?:buy|buying|sell|selling|enter|entering|exit|exiting|accumulate|accumulating|invest"
            r"|investing|add|adding|book|booking|go\s+long|short|shorting|get\s+in|get\s+out)\b"
        ),
    ),
    Rule(
        "M02_opportunity",
        Category.TIMING,
        _rx(r"\b(?:buying|selling|entry|exit)\s+opportunit(?:y|ies)\b"),
    ),
    Rule(
        "M03_now_is_time",
        Category.TIMING,
        _rx(
            r"\b(?:now|today|currently)\s+(?:is|looks\s+like|seems\s+like)?\s*(?:a\s+|the\s+)?"
            r"(?:good|great|right|ideal|best)\s+(?:time|moment)\b"
        ),
    ),
    Rule(
        "M04_urgency",
        Category.TIMING,
        _rx(
            r"\bdon'?t\s+miss\s+(?:this|the|out)\b|\bbefore\s+it'?s\s+too\s+late\b|\bact\s+(?:now|fast|quickly)\b"
        ),
    ),
    # ----------------------------------------------------------- return promises
    Rule(
        "X01_return_promise",
        Category.RETURN_PROMISE,
        _rx(
            r"\b(?:guarantee[ds]?|assured|sure)\s+(?:returns?|profits?|gains?|income|money|winner)\b"
            r"|\bmulti[\s\-]*baggers?\b|\bsure[\s\-]*shot\b"
            r"|\brisk[\s\-]*free\s+(?:returns?|profits?|trade|trades|bet)\b"
            r"|\bcan'?t\s+(?:lose|go\s+wrong)\b"
            r"|\b(?:double|triple|2x|3x|5x|10x)\s+(?:your\s+)?(?:money|investment|capital|returns?)\b"
            r"|\bjackpot\b|\bget[\s\-]*rich\b|\bzero[\s\-]*risk\b"
        ),
    ),
)
