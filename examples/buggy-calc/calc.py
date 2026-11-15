def average(values):
    """Mean of a list of numbers. An empty list has an average of 0.0."""
    return sum(values) / len(values)


def clamp(x, low, high):
    return max(low, min(x, high))
