"""Conservative, order-scoped conversation checks; no chat text is persisted."""
from __future__ import annotations

import re
from typing import Any
from FunPayAPI.common.enums import MessageTypes

# Unknown buyer messages also require review, so missed language patterns fail closed.
PROBLEM = re.compile(r"\b(?:not working|doesn.t work|can.t (?:log|login|access)|cannot (?:log|login|access)|invalid|incorrect|refund|replacement|scam|banned|locked|problem|error|wrong password|не работает|не могу|неверн\w*|ошиб\w*|возврат|замен\w*|проблем\w*|заблок\w*|не працює|помил\w*|повернен\w*|неверный)\b",re.I)
ACK = re.compile(r"(?:thank you|thanks|ty|ok|okay|got it|all good|works|it works|working|hello|hi|спасибо|спс|ок|хорошо|всё работает|все работает|привет|здравствуйте|дякую|добре|працює|merci|danke|gracias)[\s.!?,👍🙏❤️♥🙂😊]*",re.I)


def order_mentioned(text: str, order_id: str) -> bool:
    return bool(re.search(r'(?<![A-Z0-9])#?'+re.escape(order_id)+r'(?![A-Z0-9])',text,re.I))


def assess(account: Any, order: Any) -> str:
    """Empty means eligible; other values are sanitized reasons requiring a hold."""
    review=getattr(order,'review',None)
    if review is not None and review.stars < 5:
        return 'review_rating'
    messages={}
    cursor=None
    for _ in range(20):
        page=account.get_chat_history(order.chat_id,cursor,order.buyer_username)
        if not isinstance(page,list) or not page:
            return 'history_unavailable'
        for message in page:
            messages[message.id]=message
        ordered=sorted(messages.values(),key=lambda m:m.id)
        starts=[i for i,m in enumerate(ordered) if m.author_id==0 and m.type==MessageTypes.ORDER_PURCHASED
                and order_mentioned(m.text or '',order.id)]
        if starts:
            start=starts[0]
            end=next((i for i in range(start+1,len(ordered)) if ordered[i].author_id==0 and ordered[i].type==MessageTypes.ORDER_PURCHASED),len(ordered))
            window=ordered[start+1:end]
            # Later messages explicitly naming this order still belong to it.
            window += [m for m in ordered[end:] if order_mentioned(m.text or '',order.id)]
            for msg in window:
                if msg.author_id==0 and msg.type==MessageTypes.ORDER_REOPENED:
                    return 'reopened'
                if msg.author_id not in (account.id,0) and (getattr(msg,'is_employee',False) or getattr(msg,'is_support',False)):
                    return 'support'
                if msg.author_id==getattr(order,'buyer_id',None):
                    text=(msg.text or '').strip()
                    if PROBLEM.search(text):
                        return 'buyer_problem'
                    if not ACK.fullmatch(text):
                        return 'buyer_review'
            return ''
        next_cursor=ordered[0].id
        if cursor is not None and next_cursor>=cursor:
            break
        cursor=next_cursor
    return 'history_unavailable'
