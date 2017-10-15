from show import show

class scope(object):
    """An object to represent a naming scope.

    Some examples:

    >>> s = scope(None)
    >>> s.bind('kv', 'k', 1)
    >>> lookup(s, 'kv.k')
    1
    >>> lookup(s, 'k')
    1
    >>> s2 = scope(s)
    >>> lookup(s2, 'kv.k')
    1
    >>> lookup(s2, 'k')
    1
    >>> s2.bind('kv', 'k', 2)
    >>> s2.bind('kv', 'v', 42)
    >>> lookup(s2, 'kv.k'), lookup(s2, 'kv.v')
    (2, 42)
    >>> lookup(s, 'kv.k')
    1
    >>> print(lookup(s, 'kv.v'))
    None
    """

    def __init__(self, parent):
        self.parent = parent
        self.scope = {}

    def bind(self, tn, colname, idx):
        """Bind a name to a location in the memo."""
        d = self.scope.get(tn, {})
        d[colname] = idx
        self.scope[tn] = d

    def _lookup(self, tn, colname):
        """recursive function to implement lookup()"""
        if tn in self.scope:
            return self.scope[tn].get(colname, None)
        elif tn == '':
            for cols in self.scope.values():
                if colname in cols:
                    return cols[colname]
            # fallthough: we look up anonymous names in the parent
            # too.
        if self.parent is None:
            return None
        return self.parent._lookup(tn, colname)

    def __repr__(self):
        return '<%s>' % self._repr(self)

    def _repr(self, sc):
        if sc is None:
            return '{}'
        return 'scope(%r) -> %s' % (sc.scope, self._repr(sc.parent))

@show()
def lookup(sc, name):
    """Look up a name into the specified scope."""
    if '.' in name:
        tn, colname = name.split('.', 1)
        return sc._lookup(tn, colname)
    return sc._lookup('', name)

def test():
    import doctest
    print("testing...")
    doctest.testmod()
    print("testing done")

if __name__ == "__main__":
    test()

