import sys

enable_tracing = False
def set_tracing(set):
    """Enable/disable tracing of function calls globally."""
    global enable_tracing
    enable_tracing = set

_indent = 0
def show(*specialtypes):
    """This decorator dumps out the arguments passed to a function and its return value(s).
    The tracing is only active if set_tracing(True) was called previously.

    >>> set_tracing(True)
    >>> @show()
    ... def f(x, y):
    ...     return x + y
    >>> @show()
    ... def g(x):
    ...     return f(x, x) * 2
    >>> g(3)
    g(3):
        f(3, 3):
        f -> 6
    g -> 12
    12

    Optionally it also prints the updated state of the parameters that
    have the specified special type(s) at the end of the call.

    >>> @show(dict)
    ... def set(d, k, v):
    ...     d[k] = v
    ...     return v + 1
    >>> set({}, 'a', 123)
    set({}, 'a', 123):
    set -> 124
        -: {'a': 123}
    124
    """
    def wrap(func):
        fname = func.__name__
        def echo_func(*args,**kwargs):
            global enable_tracing
            if not enable_tracing:
                return func(*args, **kwargs)

            global _indent
            b = _indent
            preindent = b * ' '

            # The specialargs are those for which we want to
            # show the value post-compute.
            specialargs = []

            # Format the beginning of the first line:
            # <spaces>funcname(
            print('%s%s('%(preindent, fname), end='')
            # Indent for arguments
            argsindent = ' '*(b+len(fname)+1)
            # Format the arguments. Try to make them fit on one line.
            all = list(args) + list(kwargs.items())
            for i, a in enumerate(all):
                for special in specialtypes:
                    if isinstance(a, special):
                        specialargs.append(a)

                r = repr(a)
                r = r.replace('\n','\n'+argsindent)
                print(r, end='')
                if i+1 < len(all):
                    print(',', end='')
                    if '\n' in r:
                        print('\n'+argsindent, end='')
                    else:
                        print(' ', end='')
            print('):')

            # Now call the functions.
            _indent += 4
            ret = func(*args, **kwargs)
            _indent = b

            # Finally print the return value
            print("%s%s ->"%(preindent, fname),ret)
            if len(specialargs) > 0:
                findent = ' ' * len(fname)
                retindent = preindent + findent + ' '*4
                for a in specialargs:
                    print("%s%s -:"%(preindent,findent),
                          str(a).replace('\n','\n'+retindent))

            # Done: return the value.
            return ret
        return echo_func
    return wrap

if __name__ == "__main__":
    import doctest
    print("testing...")
    doctest.testmod()
    print("testing done")
