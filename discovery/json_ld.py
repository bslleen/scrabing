"""Shared helper for locating a schema.org JobPosting inside parsed
JSON-LD data. Used by discovery.scraper (content extraction) and
discovery.normalizer (structured field extraction) so a page's JSON-LD is
interpreted the same way in both places.
"""


def find_job_posting(node):
    """Recursively searches parsed JSON-LD data for a JobPosting object -
    handles a bare object, a list of objects, and an "@graph" wrapper.
    """
    if isinstance(node, dict):
        type_value = node.get("@type")
        types = type_value if isinstance(type_value, list) else [type_value]
        if any(isinstance(t, str) and t.lower() == "jobposting" for t in types):
            return node
        for value in node.values():
            found = find_job_posting(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_job_posting(item)
            if found:
                return found
    return None
