import sexpdata
from sexpdata import Symbol as S
from show import set_tracing

def printexp(exp):
    """Print a S-expression to the screen."""
    print(sexpdata.dumps(exp))

def loads(data):
    """Load an S-expression from a string.

    This supports some structuring types besides just lists:
    (foo args...)    for expressions
    { ... }          for sets
    [ ... ]          for arrays
    (:a x :b y :c z) for property lists (dicts)

    Examples:

    >>> q = loads('a')
    >>> q
    Symbol('a')
    >>> printexp(q)
    a

    >>> q = loads('()')
    >>> q
    []
    >>> printexp(q)
    ()

    >>> q = loads('(+ 1 2)')
    >>> q
    Exp('+', [1, 2])
    >>> printexp(q)
    (+ 1 2)

    >>> q = loads('(select :from kv)')
    >>> q
    Exp('select', (:from kv))
    >>> q.args._from
    Symbol('kv')
    >>> printexp(q)
    (select :from kv)

    >>> q = loads('{3 1 2}')
    >>> q
    Set({1, 2, 3})
    >>> printexp(q)
    {1 2 3}

    >>> q = loads('(:a 123 :b 456)')
    >>> q
    (:a 123 :b 456)
    >>> printexp(q)
    (:a 123 :b 456)
    """
    return enrich(parsesexp(data)[0])

#
# Let's fix the sexpdata library so that it supports {...} expressions too.
sexpdata.BRACKETS['{'] = '}'
import re
def parsesexp(data):
    """Parse a string to a bare S-expression."""

    p = sexpdata.Parser(data)
    # We need to re-initialize the sexpdata parser to teach it our new brackets.
    p.closing_brackets = set(sexpdata.BRACKETS.values())
    p.atom_end = \
        set(sexpdata.BRACKETS) | set(p.closing_brackets) \
        | set('"\'') | set(sexpdata.whitespace)
    p.atom_end_or_escape_re = re.compile("|".join(map(re.escape,
                                                      p.atom_end | set('\\'))))
    return p.parse()


class Set(sexpdata.SExpBase):
    """Set values: {x y z}."""
    def __init__(self, val=None):
        if val is None:
            val = set()
        elif not isinstance(val, set):
            val = set(val)
        super(Set, self).__init__(val)

    def difference(self, s):
        if isinstance(s, Set):
            s = s._val
        r = self._val.difference(s)
        if isinstance(r, set):
            return Set(r)
        return r

    def update(self, s):
        if isinstance(s, Set):
            s = s._val
        self._val.update(s)

    def difference_update(self, s):
        if isinstance(s, Set):
            s = s._val
        self._val.difference_update(s)

    def add(self, v):
        self._val.add(v)

    def tosexp(self, tosexp=sexpdata.tosexp):
        return sexpdata.Bracket(list(self.value()), '{').tosexp(tosexp)

class Props(dict):
    """A object to represent property lists.

    It's a Python dictionary with syntactic sugar.

    >>> p = Props({'a': 123})
    >>> p.a
    123
    >>> p['a']
    123
    >>> p
    (:a 123)
    """
    def __getattr__(self, k):
        if k.startswith('_'):
            k = k[1:]
        return self.get(k, None)

    def __setattr__(self, k, v):
        if k.startswith('_'):
            k = k[1:]
        super(Props, self).__setitem__(k, v)

    def __hasattr_(self, k):
        return super(Props, self).__hasattr__(k)

    def __repr__(self):
        return sexpdata.tosexp(dict(self))

class Exp(sexpdata.SExpBase):
    """An expression with a leading operator.

    >>> p = Exp('+', [1, 2])
    >>> p.op
    '+'
    >>> p.args
    [1, 2]
    >>> p
    Exp('+', [1, 2])
    >>> printexp(p)
    (+ 1 2)
    """
    def __init__(self, op, args=None):
        self.op = op
        if isinstance(args, dict):
            args = Props(args)
        self.args = args

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.op == other.op and self.args == other.args
        else:
            return False

    def __repr__(self):
        return sexpdata.uformat("Exp({0!r}, {1!r})", self.op, self.args)

    def tosexp(self, tosexp=sexpdata.tosexp):
        ret = [S(self.op)]
        if isinstance(self.args, Props):
            for k, v in self.args.items():
                ret.append(S(':'+k))
                ret.append(v)
        elif self.args is not None:
            ret += self.args
        return tosexp(ret)

def tryprops(orig, sexp, d):
    if len(sexp) == 0:
        # seen a whole dict, return it
        return d
    if len(sexp) == 1:
        # odd number of pairs, not valid dict
        return orig
    l = sexp[0]
    if not isinstance(l, S) or len(l.value()) == 0 or l.value()[0] != ':':
        # next item not a label.
        return orig
    d[l.value()[1:]] = sexp[1]
    return tryprops(orig, sexp[2:], d)

def enrich(sexp):
    """Extract structures from the S-expression."""
    if isinstance(sexp, list):
        tmp = [enrich(x) for x in sexp]
        if len(tmp) == 0:
            return tmp
        if len(tmp) > 0 and isinstance(tmp[0], S) and tmp[0].value()[0] != ':':
            op = tmp[0].value()
            rest = tryprops(tmp[1:], tmp[1:], Props())
            return Exp(op, rest)
        return tryprops(tmp, tmp, Props())

    elif isinstance(sexp, sexpdata.Bracket):
        if sexp._bra == '{':
            return Set((enrich(x) for x in sexp.value()))
        elif sexp._bra == '[':
            return [enrich(x) for x in sexp.value()]
        else:
            throw("unknown bracket" + dumps(sexp))
    elif isinstance(sexp, sexpdata.Quoted):
        return sexpdata.Quoted(enrich(sexp.val()))
    return sexp

# Main routine.
def main(handle):
    # importing readline is sufficient to activate a CLI.
    import readline
    import traceback
    import os.path
    histfile = os.path.expanduser("~/.sqlhist")
    try:
        readline.read_history_file(histfile)
    except:
        pass

    tracing = False
    while True:
        try:
            line = input("> ")
            readline.add_history(line)
            readline.write_history_file(histfile)
            if line == '\\trace':
                tracing = not tracing
                set_tracing(tracing)
                continue

        except EOFError:
            break
        try:
            q = loads(line)
        except Exception as e:
            traceback.print_exc()
            print ("invalid:", line)
            continue

        printexp(q)
        print("p:", repr(q))
        try:
            handle(q)
        except Exception as e:
            traceback.print_exc()
            continue

if __name__ == "__main__":
    print("testing...")
    import doctest
    doctest.testmod()
    print("testing done")

