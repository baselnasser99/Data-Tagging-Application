'''
Define functions that can be used in html templates
'''

# Get a value of field from object
def getattr_safe(obj, name):
    return getattr(obj, name, '')

def plural(n, word, many=None):
    """plural(1, "image") -> "1 image"; plural(3, "image") -> "3 images"."""
    return f"{n} {word if n == 1 else (many or word + 's')}"
