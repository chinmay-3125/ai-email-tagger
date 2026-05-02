"""Priority tagging for classified emails."""

def tag_priority(subject: str, body: str) -> str:
    """Determine priority tag based on subject and body content."""
    text = (subject + " " + body).lower()
    
    urgent_keywords = [
        "urgent", "asap", "immediately", "deadline", "due today", "overdue",
        "action required", "time sensitive", "expires today", "last chance",
        "final notice", "critical", "emergency"
    ]
    if any(kw in text for kw in urgent_keywords):
        return "Urgent"
        
    needs_reply_keywords = [
        "please reply", "reply needed", "waiting for your response",
        "can you confirm", "let me know", "your thoughts", "follow up",
        "get back to me", "awaiting your response", "please respond",
        "rsvp", "kindly revert"
    ]
    if any(kw in text for kw in needs_reply_keywords):
        return "Needs Reply"
        
    important_keywords = [
        "invoice", "payment due", "your account", "security alert",
        "password reset", "verify your email", "bank statement",
        "booking confirmed", "ticket", "order confirmed", "interview",
        "offer letter", "contract"
    ]
    if any(kw in text for kw in important_keywords):
        return "Important"
        
    spam_keywords = [
        "unsubscribe", "click here", "you have won", "free gift",
        "limited offer", "buy now", "discount", "sale ends",
        "earn money", "make money fast", "congratulations you",
        "no cost", "risk free"
    ]
    if any(kw in text for kw in spam_keywords):
        return "Spam"
        
    return "FYI"
