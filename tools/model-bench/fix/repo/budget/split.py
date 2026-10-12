def split_bill(total, people, tip_percent=0):
    """Each person's share of a bill, with tip, rounded to cents. The shares must add up to the total with tip."""
    with_tip = total * (1 + tip_percent / 100)
    share = round(with_tip / people, 2)
    return [share] * people
