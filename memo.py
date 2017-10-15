import sexpdata
import io
from show import show
from sqlio import Exp, Props

class cls(object):
    """An object that represents an expression class.

    A class has:
    - a set of properties.
    - zero or more m-expressions: structural representations
      of the expressions, or in other words a "strategy"
      to compute the results defined by the class.

    This implementation is intended for use together with sqlio.Props
    for properties. Any m-expression and property type that can render
    as a S-expression (via sexpdata.dumps) can be used.

    Examples:

    >>> cls(Exp('+'))
    <cls (+)                                      ()>
    >>> cls(Exp('+', [1, 2]), {'a': 1, 'b': 2})
    <cls (+ 1 2)                                  (:a 1 :b 2)>
    """
    def __init__(self, mexpr, props=None):
        if props is None:
            props = Props()
        if isinstance(props, dict):
            props = Props(props)
        self.props = props
        self.mexprs = [mexpr]

    def __repr__(self):
        return "<cls %-40s %s>" % (' '.join(sexpdata.dumps(e) for e in self.mexprs),
                                    sexpdata.dumps(self.props))

class memo(object):
    """An object that represents a memo.

    A memo is an array of classes, with a "root" index that points to
    the class at the top of the expression tree.

    Example:

    >>> m = memo()
    >>> idx1 = m.newcls(Exp('+'), {'a': 123})
    >>> idx2 = m.newcls(Exp('-', [idx1]), {'b': 456})
    >>> m.root = idx2
    >>> print(m)
    <memo
    root: 1
     0 <cls (+)                                      (:a 123)>
     1 <cls (- 0)                                    (:b 456)>
    >
    """
    def __init__(self):
        self.root = None
        self.classes = []

    def __getitem__(self, idx):
        """A memo supports the m[idx] notation."""
        return self.classes[idx]

    def newcls(self, item, props):
        self.classes.append(cls(item, props))
        return len(self.classes)-1

    def __repr__(self):
        return memo_as_string(self)

def memo_as_string(m):
    """Render a memo as a string."""
    s = io.StringIO()
    print("<memo\nroot:", m.root, file=s)
    for i, c in enumerate(m.classes):
        print("%2d"%i, c, file=s)
    print(">", file=s, end='')
    return s.getvalue()

import sys
def print_tree(m):
    """Show a memo as an expression tree.

    This displays SQL relational operators as a tree,
    and inlines scalar expressions together.

    The following operators are recognized as relational:
    project, filter, scan, cross.

    For example:

    >>> m = memo()
    >>> a = m.newcls(Exp('lit', [123]), {})
    >>> b = m.newcls(Exp('lit', [456]), {})
    >>> apb = m.newcls(Exp('+', [a, b]), {})
    >>> s = m.newcls(Exp('scan', ['kv']), {'foo': 'bar'})
    >>> f = m.newcls(Exp('filter', [s, apb]), {'hello':'world'})
    >>> m.root = f
    >>> print_tree(m)
    ( 4) filter
         props:
              :hello "world"
         filter (+ 123 456)
    <BLANKLINE>
        ( 3) scan
             props:
                  :foo "bar"
             table kv
    <BLANKLINE>
    """
    _printtree(0, m.root, m, sys.stdout)

# Helper function for print_tree().
def _printtree(indent, idx, m, buf):
    prefix = indent*' '
    e = m[idx].mexprs[0]
    rest = io.StringIO()
    print('%s(%2d) %s' % (prefix, idx, e.op), file=buf)
    print('%s     props:' % prefix, file=buf)
    for k, v in m[idx].props.items():
        print('%s          :%s %s' % (prefix, k, sexpdata.dumps(v)), file=buf)
    if e.op == 'project':
        print('%s     exprs' % prefix, ', '.join((_printscalar(indent, i, m, rest) for i in m[idx].props.cols)), file=buf)
    elif e.op == 'filter':
        print('%s     filter' % prefix, _printscalar(indent, e.args[1], m, rest), file=buf)
    elif e.op == 'scan':
        print("%s     table" % prefix, e.args[0])
    print(file=buf)
    if e.op in ['project', 'filter']:
        _printtree(indent+4, e.args[0], m, buf)
    elif e.op in ['cross']:
        for e in e.args:
            _printtree(indent+4, e, m, buf)
    rest = rest.getvalue()
    if len(rest) > 0:
        print('%s----' % prefix,file=buf)
        print(rest,file=buf)

# Helper function for print_tree().
def _printscalar(indent, i, m, buf):
    def ps(exp):
        if exp.op == 'lit':
            return sexpdata.tosexp(exp.args[0])
        elif exp.op == 'var':
            return '(@%d %s)' % (i, exp.args[0])
        elif exp.op in ['apply', 'exists']:
            _printtree(indent, exp.args[0], m, buf)
            return sexpdata.tosexp(exp)
        else:
            return '(%s %s)' % (exp.op,
                                ' '.join(_printscalar(indent, i, m, buf) for i in exp.args))

    return ps(m[i].mexprs[0])

if __name__ == "__main__":
    print("testing...")
    import doctest
    doctest.testmod()
    print("testing done")
