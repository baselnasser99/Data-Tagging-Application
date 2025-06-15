'''
Define functions that can be used in html templates
'''

# Get a value of field from object
def getattr_safe(obj, name):
    return getattr(obj, name, '')