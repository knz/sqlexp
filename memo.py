import sexpdata
import io
from show import show
from sqlio import Exp, Props

class cls(object):
    """An object that represents a class.

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
                                    repr(self.props))

class memo(object):
    """An object that represents a memo."""
    def __init__(self):
        self.root = None
        self.classes = []

    def __getitem__(self, idx):
        return self.classes[idx]

    def newcls(self, item, props):
        self.classes.append(cls(item, props))
        return len(self.classes)-1

    def setroot(self, root):
        self.root = root

    def __repr__(self):
        s = io.StringIO()
        print("<memo\nroot:", self.root,file=s)
        for i, c in enumerate(self.classes):
            print(i, c, file=s)
        print(">",file=s,end='')
        return s.getvalue()

import sys
def print_tree(m):
    """Show a memo as an expression tree"""
    _printtree(0, m.root, m, sys.stdout)

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

