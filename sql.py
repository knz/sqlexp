import sexpdata
from sexpdata import Symbol as S
from show import show
from scope import scope,lookup
from sqlio import *
from memo import memo, print_tree
import io

# ... Some fake table data ...
tables = {
    'kv': ('k', 'v'),
    'ab': ('a', 'b'),
}

## ... Some simple helpers...
def throw(s):
    raise Exception(s)

def op(exp):
    if isinstance(exp, Exp):
        return exp.op
    return None

@show(memo)
def add_scalar_exp(memo, mexpr, props):
    assert 'neededcols' in props
    return memo.newcls(mexpr, props)

@show(memo)
def add_rel_exp(memo, mexpr, props):
    assert 'outs' in props
    assert 'cols' in props
    assert 'labels' in props
    # For relational expressions, neededcols
    # is the set of free variables in the expression
    # (correlation dependencies)
    assert 'neededcols' in props
    return memo.newcls(mexpr, props)


@show(memo,scope)
def get_datasource(memo, env, exp):
    if isinstance(exp, S):
        tn = exp.value()
        if tn not in tables:
            throw("unknown table: %s" % tn)
        t = tables[tn]
        vars = []
        lbls = []
        for colnum, colname in enumerate(t):
            varidx = add_scalar_exp(memo, Exp('var', ['%s.%s'%(tn,colname)]), {'neededcols':None})
            memo[varidx].props.neededcols = Set([varidx])
            env.bind(tn, colname, varidx)
            vars.append(varidx)
            lbls.append(colname)

        return add_rel_exp(memo, Exp('scan', [tn]), {'cols':vars,'outs':Set(vars),'labels':lbls, 'neededcols':Set()})

    elif op(exp) == 'select':
        # Oh, a subquery!
        srcidx = analyze_select(memo, env, exp)
        # Make its columns visible in the environment.
        for l, idx in zip(memo[srcidx].props.labels, memo[srcidx].props.cols):
            env.bind('', l, idx)
        # Determine the remaining needed columns - those not provided by the select itself
        outs = memo[srcidx].props.outs
        memo[srcidx].props.neededcols.difference_update(outs)
        return srcidx

    throw("unknown from clause: %r" % exp)

@show(memo,scope)
def analyze_from(memo, env, exp):
    if isinstance(exp, list):
        # Cross-join in disguise.
        idxs, lbls, cols, outs = [], [], [], Set()
        for e in exp:
            idx = get_datasource(memo, env, e)
            idxs.append(idx)
            lbls.extend(memo[idx].props.labels)
            cols.extend(memo[idx].props.cols)
            outs.update(memo[idx].props.outs)
        return add_rel_exp(memo, Exp('cross', idxs), {'cols':cols, 'outs':outs,'labels':lbls,'neededcols':Set()})

    return get_datasource(memo, env, exp)

def tocolname(exp):
    return sexpdata.dumps(exp)

@show(memo,scope)
def analyze_select(memo, env, exp):
    assert op(exp) == 'select'

    here = scope(env)

    if exp.args._from is not None:
        srcidx = analyze_from(memo, here, exp.args._from)
    else:
        srcidx = add_rel_exp(memo, Exp('unary'), {'cols':[],'outs':Set(),'labels':[],'neededcols':Set()})

    if exp.args._where is not None:
        fidx = analyze_scalar(memo, here, exp.args._where)
        srcidx = add_rel_exp(memo,
                                Exp('filter', [srcidx, fidx]),
                                {'cols': memo[srcidx].props.cols,
                                 'outs':memo[srcidx].props.outs,
                                 'labels':memo[srcidx].props.labels,
                                 'neededcols':memo[fidx].props.neededcols.difference(memo[srcidx].props.cols),
                                })

    if exp.args._exprs is not None:
        exprs = exp.args._exprs
        if not isinstance(exprs, list):
            exprs = [exprs]
        labels = []
        eexprs = []
        for e in exprs:
            if not isinstance(e, Props):
                labels.append(tocolname(e))
                eexprs.append(e)
            else:
                k = list(e.keys())[0]
                labels.append(k)
                eexprs.append(e[k])
        idxs = [analyze_scalar(memo, here, e) for e in eexprs]
        outs = Set(idxs)
        if idxs == memo[srcidx].props.cols and labels == memo[srcidx].props.labels:
            # (select x as a from (select y as x from yt)) -> (select y as a from yt)
            pass
        elif outs == memo[srcidx].props.outs:
            srcidx = add_rel_exp(memo, memo[srcidx].mexprs[0], {'cols':idxs,'outs':outs,'labels':labels,
                                                                'neededcols':memo[srcidx].props.neededcols})
        else:
            srcidx = add_rel_exp(memo,
                             Exp('project', [srcidx]),
                             {'cols':idxs, 'outs':outs, 'labels':labels,
                              'neededcols':memo[srcidx].props.neededcols.difference(outs),
                             })

    return srcidx

@show(memo,scope)
def analyze_scalar(memo, env, exp):
    if isinstance(exp, S):
        # some column reference
        idx = lookup(env, exp.value())
        if idx is None:
            throw("unknown column: %s" % exp)
        return idx

    elif isinstance(exp, int):
        # some literal
        return add_scalar_exp(memo, Exp('lit', [exp]), {'neededcols':Set()})

    elif op(exp) == 'exists':
        idx = analyze_select(memo, env, exp.args[0])
        return add_scalar_exp(memo, Exp('exists', [idx]), {'neededcols':memo[idx].props.neededcols})

    elif op(exp) == 'select':
        idx = analyze_select(memo, env, exp)
        return add_scalar_exp(memo, Exp('apply', [idx]), {'neededcols':memo[idx].props.neededcols})
        # throw("unhandled subquery: %s" % exp)

    elif isinstance(exp, Exp):
        # some operator with operands
        if not isinstance(exp.args, list):
            throw("unknown scalar expr type: %s" % exp)
        idxs = [analyze_scalar(memo, env, e) for e in exp.args]
        neededcols = Set()
        for i in idxs:
            neededcols.update(memo[i].props.neededcols)
        return add_scalar_exp(memo, Exp(exp.op, idxs), {'neededcols':neededcols})

    else:
        throw("unknown scalar expression: %s" % exp)


def handlesql(exp):
    m = memo()
    srcidx = analyze_select(m, scope(None), exp)
    m.setroot(srcidx)
    print("memo after analysis:")
    print(m)
    print("expression tree:")
    print_tree(m)


############################## I/O routines #################################


if __name__ == "__main__":
    print("testing...")
    import doctest
    doctest.testmod()
    print("testing done")

    main(handlesql)
