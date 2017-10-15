"""
The code here illustrates how to use a memo data structure to analyze
a SQL query.

It can be launched as a CLI shell with

   python3 sql.py

It can also be used as a library.

Example:

>>> q = '(select :exprs (+ k v) :from kv)' # SELECT k+v FROM kv
>>> handle_sql(loads(q))
memo after analysis:
<memo
root: 4
 0 <cls (var "kv.k")                             (:neededcols {0})>
 1 <cls (var "kv.v")                             (:neededcols {1})>
 2 <cls (scan "kv")                              (:cols (0 1) :outs {0 1} :labels ("k" "v") :neededcols {})>
 3 <cls (+ 0 1)                                  (:neededcols {0 1})>
 4 <cls (project 2)                              (:cols (3) :outs {3} :labels ("(+ k v)") :neededcols {})>
>
expression tree:
( 4) project
     props:
          :cols (3)
          :outs {3}
          :labels ("(+ k v)")
          :neededcols {}
     exprs (+ (@0 kv.k) (@1 kv.v))
<BLANKLINE>
    ( 2) scan
         props:
              :cols (0 1)
              :outs {0 1}
              :labels ("k" "v")
              :neededcols {}
         table kv
<BLANKLINE>

Some other examples that can be fed to this analyzer:

# SELECT k FROM kv WHERE TRUE = (SELECT a FROM ab WHERE b = v)
(select :exprs k :from kv :where (= t (select :exprs a :from ab :where (= b v))))

# SELECT k,a FROM kv, ab
(select :exprs [k a] :from [kv ab])

# SELECT x AS k FROM (SELECT v AS x FROM kv)
(select :exprs [(:k x)] :from (select :exprs [(:x v)] :from kv))

"""

import sexpdata
from sexpdata import Symbol as S
from show import show
from scope import scope,lookup
from sqlio import *
from memo import memo, print_tree
import io

@show(memo)
def add_scalar_exp(memo, mexpr, props):
    """Add a scalar expression class into the memo.

    The class is defined by an initial m-expression and a set of
    properties. The required properties for scalar expressions are
    checked here.
    """

    # We want all scalar expressions to have a set of needed columns.
    # This will be used to detect correlation.
    assert 'neededcols' in props

    return memo.newcls(mexpr, props)

@show(memo,scope)
def analyze_scalar(memo, env, exp):
    """Analyzes a scalar expression and populates the memo accordingly.

    The argument are as follows:
    - memo: the memo to populate.
    - env: the current naming scope, used to resolve column references.
    - exp: the expression to analyze.

    The return value is the memo index of the top level node (class)
    that implements the expression.
    """

    if isinstance(exp, S):
        # A column reference: look up the index of the variable in the
        # current scope, and return that as result.
        idx = lookup(env, exp.value())
        if idx is None:
            throw("unknown column: %s" % exp)
        return idx

    elif isinstance(exp, int):
        # A literal: add it to the memo. No column is needed.
        return add_scalar_exp(memo, Exp('lit', [exp]), {'neededcols':Set()})

    elif op(exp) == 'exists':
        # EXISTS(...subquery...)
        #
        # Analyze the subquery as a relational expression, populate
        # the memo, then create a scalar m-expression that refers to it.
        #
        # The needed columns are those needed by the subquery. This is
        # usually empty, except for correlated subqueries.
        #
        idx = analyze_select(memo, env, exp.args[0])
        return add_scalar_exp(memo, Exp('exists', [idx]),
                              {'neededcols':memo[idx].props.neededcols})

    elif op(exp) == 'select':
        # subquery in scalar context, e.g. (SELECT ... ) > 3
        #
        # Analyze the subquery as a relational expression, populate
        # the memo, then create an Apply scalar m-expression that
        # refers to it.
        # Same handling as EXISTS above, really.
        idx = analyze_select(memo, env, exp)
        return add_scalar_exp(memo, Exp('apply', [idx]),
                              {'neededcols':memo[idx].props.neededcols})

    elif isinstance(exp, Exp):
        # A scalar operator.
        # Analyze the operands recursively as scalars, then
        # populate the memo with an expression using the
        # operand memo indexes as operands.
        if not isinstance(exp.args, list):
            throw("unknown scalar expr type: %s" % exp)

        # Recurse into operands, get indexes.
        idxs = [analyze_scalar(memo, env, e) for e in exp.args]
        # The set of needed columns for the new node is the union
        # of the needed columns for the operands.
        # With a bitmap this is just a bitwise OR of the needed masks.
        neededcols = Set()
        for i in idxs:
            neededcols.update(memo[i].props.neededcols)
        # Make the new node.
        return add_scalar_exp(memo, Exp(exp.op, idxs), {'neededcols':neededcols})

    else:
        throw("unknown scalar expression: %s" % exp)

@show(memo)
def add_rel_exp(memo, mexpr, props):
    """Add a relational expression class into the memo.

    The class is defined by an initial m-expression and a set of
    properties. The required properties for relational expressions are
    checked here.
    """

    # 'outs' is the set of columns provided by this relational expression.
    assert 'outs' in props

    # 'cols' is the list of column indexes that defines in which order
    # the output columns are presented in each result row.
    assert 'cols' in props

    # 'labels' is the list of column labels.
    assert 'labels' in props

    # For relational expressions, neededcols is the set of free
    # variables in the expression (correlation dependencies). This is
    # defined as the union of all columns needed by scalar
    # sub-expressions, minus (set difference) all columns provided by
    # the relational expression.
    assert 'neededcols' in props

    return memo.newcls(mexpr, props)


@show(memo,scope)
def analyze_select(memo, env, exp):
    """Analyzes a SELECT relational expression and populates the memo accordingly.

    The argument are as follows:
    - memo: the memo to populate.
    - env: the current naming scope, used to resolve column references.
    - exp: the expression to analyze. Must have operator 'select'.

    The return value is the memo index of the top level node (class)
    that implements the expression.
    """
    assert op(exp) == 'select'

    # Each SELECT clause starts a new naming scope: all the names
    # defined by the sources in its FROM clause do not leak in the
    # surrounding context -- only the names of the projected
    # expressions will be visible, as column labels in the resulting
    # memo class.
    here = scope(env)

    # The structure of a SELECT clause is always in the following order.
    # source in FROM clause -> WHERE (filter) -> grouping -> projection -> sorting -> limit.
    # Each stage is optional and has the previous one as child (source) in the tree.

    # Analyze FROM.
    if exp.args._from is not None:
        # Populate the memo with the data source(s). See below.
        srcidx = analyze_from(memo, here, exp.args._from)
    else:
        # No FROM clause: we need to use the unary pseudo-table.
        srcidx = add_rel_exp(memo, Exp('unary'),
                             {'cols':[],'outs':Set(),'labels':[],'neededcols':Set()})

    # Analyze WHERE.
    if exp.args._where is not None:
        # The WHERE clause is a scalar expression.
        fidx = analyze_scalar(memo, here, exp.args._where)
        # Make a filter node. The filter node propagates the result
        # columns and labels from the FROM source.
        #
        # The columns provided by the source are substracted from the
        # needed columns in the filter to determine the remaining
        # needed columns after the filter stage.
        srcidx = add_rel_exp(memo, Exp('filter', [srcidx, fidx]),
                             {'cols': memo[srcidx].props.cols,
                              'outs': memo[srcidx].props.outs,
                              'labels': memo[srcidx].props.labels,
                              'neededcols':memo[fidx].props.neededcols.difference(memo[srcidx].props.cols),
                             })

    # XXX: we don't support GROUP BY here yet.

    # Analyze the projection targets.
    if exp.args._exprs is not None:
        exprs = exp.args._exprs

        # To facilitate testing, we provide some syntactic sugar so that
        # the user can omit the grouping braces when there is just one projection:
        # (select :exprs 123) == (select :exprs [123])
        if not isinstance(exprs, list):
            exprs = [exprs]

        # A projection is defined by a scalar expression and a column label.
        # If the column label is not specified, we use a text representation
        # of the scalar expression as label.
        #
        # For example:
        #
        # (select :exprs [ (:a (* k 2)) (:b (+ v 3)) ])
        #
        # == SELECT k*2 AS a, v+3 AS b
        #
        # Again we facilitate testing with syntactic sugar: the user
        # can omit the label, in which case it is computed automatically.
        labels, eexprs = [], []
        for e in exprs:
            if not isinstance(e, Props):
                # Label omitted.
                labels.append(tocolname(e))
                eexprs.append(e)
            else:
                # (:lbl <expr>)
                k = list(e.keys())[0]
                labels.append(k)
                eexprs.append(e[k])

        # Analyze all the projection targets, and collect their memo indices.
        idxs = [analyze_scalar(memo, here, e) for e in eexprs]
        # The set of output columns for the projection is precisely the
        # set of projected expressions.
        outs = Set(idxs)

        # Small optimizations:
        if idxs == memo[srcidx].props.cols and labels == memo[srcidx].props.labels:
            # If are projecting exactly the columns of the source with the same names
            # and in the same order, we can omit the projection altogether.
            #
            # (select x as a from (select y as x from yt)) == (select y as a from yt)
            #
            pass

        elif outs == memo[srcidx].props.outs:
            # If we are projecting exactly the columns of the source but with
            # a different order and/or different columns, then *copy* the
            # original source node with the new order and labels.
            srcidx = add_rel_exp(memo, memo[srcidx].mexprs[0],
                                 {'cols':idxs,'outs':outs,'labels':labels,
                                  'neededcols':memo[srcidx].props.neededcols})
        else:
            # General case: add a projection node.
            #
            # Like for the filter node, the needed columns
            # (correlation dependencies) for the projection is the
            # union of needed columns for the projection expressions,
            # minus the columns provided by this SELECT clause.
            srcidx = add_rel_exp(memo, Exp('project', [srcidx]),
                                 {'cols':idxs, 'outs':outs,
                                  'labels':labels,
                                  'neededcols':memo[srcidx].props.neededcols.difference(outs),
                                 })

    # XXX: we don't support ORDER BY here yet.
    # XXX: we don't support LIMIT here yet.

    # The result of the analysis is the memo index of the last node
    # constructed.
    return srcidx


# tables is the list of tables known for the purpose
# of schema resolution in FROM clauses (get_datasource below).
tables = {
    # ... Some fake table schema ...
    # (for testing)
    'kv': ('k', 'v'),
    'ab': ('a', 'b'),
}

@show(memo,scope)
def analyze_from(memo, env, exp):
    """Analyzes a FROM clause.

    The real work is performed by get_datasource() below.

    However, FROM a,b,c is really equivalent to FROM a CROSS JOIN b
    CROSS JOIN c. We handle that in analyze_from().
    """
    if not isinstance(exp, list):
        # Common case: delegate to get_datasource() below.
        return get_datasource(memo, env, exp)

    # Otherwise: cross-join in disguise.
    idxs, lbls, cols, outs = [], [], [], Set()
    for e in exp:
        idx = get_datasource(memo, env, e)
        idxs.append(idx)
        lbls.extend(memo[idx].props.labels)
        cols.extend(memo[idx].props.cols)
        outs.update(memo[idx].props.outs)
        # XXX: need to collect names and needed columns side-ways
        # to support lateral (correlated) joins.

    return add_rel_exp(memo, Exp('cross', idxs),
                       {'cols':cols, 'outs':outs,'labels':lbls,'neededcols':Set()})

@show(memo,scope)
def get_datasource(memo, env, exp):
    """Analyzes a relational expression in a FROM clause.

    This also populates the current naming scope with
    the columns it defines, so that the surrounding SELECT
    clause can use these column names in scalar expressions.
    """
    if isinstance(exp, S):
        # A simple table name: generate a scan.
        tn = exp.value()
        if tn not in tables:
            throw("unknown table: %s" % tn)
        t = tables[tn]
        vars, lbls = [], []
        for colnum, colname in enumerate(t):
            # Generate a variable in the memo.
            varidx = add_scalar_exp(memo, Exp('var', ['%s.%s'%(tn,colname)]), {'neededcols':None})
            # Make the name of the column available in the current scope.
            env.bind(tn, colname, varidx)
            # Make the variable be its own needed column set. This is
            # a bit weird and doesn't fit the definition of "needed
            # columns" exactly, but it streamlines the computation of
            # needed columns in analyze_scalar(): all scalar
            # expressions simply have the union of their operands as
            # needed set.
            memo[varidx].props.neededcols = Set([varidx])
            # Prepare the output sets for the new scan node.
            vars.append(varidx)
            lbls.append(colname)

        # Note: a scan does not have any needed columns, it's always decorrelated.
        return add_rel_exp(memo, Exp('scan', [tn]),
                           {'cols':vars,'outs':Set(vars),'labels':lbls,'neededcols':Set()})

    elif op(exp) == 'select':
        # Oh, a subquery!
        # In a FROM clause, it's actually rather simple. Just
        # analyze the clause, this will populate the memo properly.
        srcidx = analyze_select(memo, env, exp)
        # However, the names defined in the clause do not leak in
        # the environment because it uses its own clause.
        # Only the output (projected) columns are visible in the environment
        # of the current FROM clause. Do that here.
        for l, idx in zip(memo[srcidx].props.labels, memo[srcidx].props.cols):
            env.bind('', l, idx)
        # Determine the remaining needed columns - those not provided
        # by the select itself.
        outs = memo[srcidx].props.outs
        memo[srcidx].props.neededcols.difference_update(outs)

        return srcidx

    throw("unknown from clause: %r" % exp)

def tocolname(exp):
    """Generates a label for a projection column."""
    return sexpdata.dumps(exp)

def handle_sql(exp):
    """Function to handle one input S-expression.

    This prepares the expression, then prints the memo
    and expression tree.
    """

    # Compile the expression.
    m = memo()
    m.root = analyze_select(m, scope(None), exp)

    # Print the results.
    print("memo after analysis:")
    print(m)
    print("expression tree:")
    print_tree(m)

# simple helper to simplify the syntax.
def throw(s):
    raise Exception(s)

# simple helper to simplify the syntax.
def op(exp):
    if isinstance(exp, Exp):
        return exp.op
    return None


if __name__ == "__main__":
    print("testing...")
    import doctest
    doctest.testmod()
    print("testing done")

    # Ask sqlio for a main loop, with us as callback.
    main(handle_sql)
