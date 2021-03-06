import ast
from copy import deepcopy
from sys import exit, stdout
from collections import defaultdict
from unparser import AstToCython
from StringIO import StringIO


from sys import argv
if len(argv) != 3:
    print "Usage: autopar.py <program.pyx> <num_threads>"
    exit(-1)

program = open(argv[1], 'r').read()

#print "####\n", program, "####"


try:
  num_thrs = int(argv[2])
except:
  print "Second argument must be integer"
  exit(-1)

# Number of threads to cythonize loops with:
NUM_THREADS = num_thrs

# For identifying array accesses
UNIQUE_ID = 0
PARENT_LOOP_UID = 0
# LOOP_UID back to its AST object:
INVERSE_MAPPING = {}

def cprint(node, s):
  """ Print s indented by node.col_offset """
  print " "*node.col_offset + s

######################################################
"""###################################################
AST Modifiers:
"""###################################################
######################################################
class ReplaceWithConstant(ast.NodeTransformer):
  """ Replace variable with identifier id with a constant """
  def __init__(self, constant, _id):
    self.constant = constant; self.id = _id
  def visit_Name(self, node):
    self.generic_visit(node) # visit children
    if isinstance(node, ast.Name) and node.id == self.id:
      return ast.Num(self.constant)
    else:
      return node # don't change

######################################################
"""###################################################
Expression map builders and wrappers:
"""###################################################
######################################################
def build_expression_map(v, exp):
  exp_map = []

  for i in xrange(v.lower, v.upper, v.step):
    _exp = deepcopy(exp)
    ReplaceWithConstant(i, v.id).visit(_exp)
    ast.fix_missing_locations(_exp)
    exp_val = eval(compile(ast.Expression(_exp), filename="<ast>", mode="eval"))
    exp_map += [(_LHS, LHS_val, _RHS, RHS_val)]

  return exp_map

## Deprecated...
def computeExpression(exp_map, i, typ):
  if   typ == "LHS": return exp_map[i][1]
  elif typ == "RHS": return exp_map[i][3]
  else: print('invalid type'); assert(False)

######################################################
"""###################################################
Expression and Subscript string building and printing:
"""###################################################
######################################################
def opToStr(op):
  if isinstance(op, ast.Add): return "+"
  if isinstance(op, ast.Sub): return "-"
def buildExpressionString(E):
  bes = buildExpressionString
  if isinstance(E, ast.BinOp):
    return bes(E.left) + opToStr(E.op) + bes(E.right)
  if isinstance(E, ast.Name):
    return E.id
  if isinstance(E, ast.Num):
    return str(E.n)
def printExpression(E):
  print buildExpressionString(E)
def buildSubscriptString(S):
  arrayName = S.value.id
  index = S.slice.value
  return arrayName + "[" + buildExpressionString(index) + "]"
def printSubscript(S):
  assert(isinstance(S, ast.Subscript))
  print buildSubscriptString(S)

######################################################
"""###################################################
ArrayAccess and Iterator class definitions:
"""###################################################
######################################################
class ArrayAccess(object):
  def __init__(self, _array_name, _indexing_exp, _access_type, _iterators, _lineno):
    self.array_name = _array_name
    self.access_type = _access_type
    self.indexing_exp = _indexing_exp
    self.iterators = _iterators
    self.lineno = _lineno
    global UNIQUE_ID
    self.unique_id = UNIQUE_ID
    UNIQUE_ID += 1
  def __str__(self):
    return "ACCESS_" + str(self.unique_id) + \
           "{" + \
              self.array_name + \
              "[" + buildExpressionString(self.indexing_exp) + "]" + \
              " " + self.access_type + " " + "line:"+str(self.lineno) + \
              " " + str(self.iterators) + "}"
  def __repr__(self):
    return self.__str__()

class Iterator(object):
  def __init__(self, _id, lower, upper,  _depth, step=1):
    self.id    = _id;     self.step  = step
    self.lower = lower.n; self.upper = upper.n
    self.depth = _depth
  def __str__(self):
    return "ITER{" + str(self.depth) + ", " + \
           self.id + ":" + "[" +   \
           str(self.lower) + "," + \
           str(self.upper) + "," + \
           str(self.step) + "]}"
  def __repr__(self): return self.__str__()

######################################################
"""###################################################
AST node visitor class definition:
"""###################################################
######################################################
class ArrayVisitor(ast.NodeVisitor):
  """ Visit array assignments / uses to gather
      information to use in array dependence
      analysis within a For loop """
  def __init__(self, _newLoopVar, _allLoopVars, _parent_loop_uid):
    self.arrays = []
    self.loopVars = _allLoopVars + [_newLoopVar]
    self.arrayAccesses = []
    self.arrayWrites = []
    self.parent_loop_uid = _parent_loop_uid
  
  # When finiding a for loop, get it its iterator,
  #   calculate one level deeper,
  #   and look for more array accesses
  def visit_For(self, node):
    #cprint(node, "Nested for found")
    global mynode1, INVERSE_MAPPING
    mynode1 = node

    loop_var_id, iter_name, iter_args = node.target.id, node.iter.func.id, node.iter.args
    INVERSE_MAPPING[self.parent_loop_uid][1] += [loop_var_id]

    iter_start, iter_end = iter_args[0], iter_args[1]

    assert(len(iter_args) in [1,2]) # TODO: add 3 for step argument
    assert(iter_name == "xrange" or iter_name == "range")

    next_depth = self.loopVars[-1].depth + 1 # going one loop deeper

    newLoopVar = Iterator(loop_var_id, iter_start, iter_end, next_depth)
    newArrVisitor = ArrayVisitor(newLoopVar, self.loopVars, self.parent_loop_uid)
    for subnode in node.body:
      newArrVisitor.visit(subnode)


  def visit_AugAssign(self, node):
    global allWriteAccesses
    allWriteAccesses[self.parent_loop_uid].append(-1)
  """
    RHS_accesses = []
    for subnode in ast.walk(node.value):
      if isinstance(subnode, ast.Subscript):
        RHS_accesses.append(subnode)
    for right in RHS_accesses:
      right_array_name, right_indexing_exp, right_access_type = \
        right.value.id, right.slice.value, "READ"
      arrAccessRead = \
        ArrayAccess(right_array_name, right_indexing_exp,
                    right_access_type, deepcopy(self.loopVars),
                    right.lineno)
      allAccesses[self.parent_loop_uid] += [arrAccessRead]
      INVERSE_MAPPING[self.parent_loop_uid][2] += [right_array_name]
    self.visit_Assign(node)
  """

  # When finding an assignment statement,
  #   look for Subscripts and assign their array accesses
  #   to just the global write access list or both
  #   the aforementione list and the all accesses list
  def visit_Assign(self, node):
    global LHS, RHS, hi, mynode, allAccesses, allWriteAccesses, INVERSE_MAPPING
    #cprint(node, "Assign found")
    mynode = node

    LHS_accesses = []
    for subnode in ast.walk(node.targets[0]):
      if isinstance(subnode, ast.Subscript):
        mynode = subnode
        LHS_accesses.append(subnode)

    RHS_accesses = []
    for subnode in ast.walk(node.value):
      if isinstance(subnode, ast.Subscript):
        RHS_accesses.append(subnode)

    for left in LHS_accesses:
      left_array_name, left_indexing_exp, left_access_type = \
        left.value.id, left.slice.value, "WRITE"
      arrAccessWrite = \
        ArrayAccess(left_array_name, left_indexing_exp,
                    left_access_type, deepcopy(self.loopVars),
                    left.lineno)
      allWriteAccesses[self.parent_loop_uid] += [arrAccessWrite]
      allAccesses[self.parent_loop_uid] += [arrAccessWrite]
      INVERSE_MAPPING[self.parent_loop_uid][2] += [left_array_name]


    for right in RHS_accesses:
      right_array_name, right_indexing_exp, right_access_type = \
        right.value.id, right.slice.value, "READ"
      arrAccessRead = \
        ArrayAccess(right_array_name, right_indexing_exp,
                    right_access_type, deepcopy(self.loopVars),
                    right.lineno)
      allAccesses[self.parent_loop_uid] += [arrAccessRead]
      INVERSE_MAPPING[self.parent_loop_uid][2] += [right_array_name]

    """
    for writeAccess in allWriteAccesses:
      writeDepth = len(writeAccess.iterators) - 1
      otherDepth = len(access.iterators) - 1

      # Check if write affects other access
      #   i.e. on the same or deeper depth
      if writeDepth >= otherDepth:
    """
    """
    assert(len(node.targets) == 1)
    v = self.loopVar

    # Generate a mapping of
    #   Constant -> Expression computed with Value replaced with Constant
    exp_map = build_expression_map(v, LHS, RHS)

    has_independent_iterations = True
    print v.lower, v.upper, v.step
    for i in xrange(v.lower, v.upper, v.step):
      LHS_val = computeExpression(exp_map, i, "LHS")
      for j in xrange(v.lower, v.upper, v.step):
        RHS_val = computeExpression(exp_map, j, "RHS")

        print LHS_val, RHS_val

        if LHS_val == RHS_val:
          has_independent_iterations = False; break
      # break out of nested loop
      if has_independent_iterations == False: break
          

    print has_independent_iterations, LHS_val, RHS_val
    """


def DP(doflsts):
  # Base:
  if len(doflsts) == 1:
    d_lst = []
    for itr, vals in doflsts.items():
      for val in vals:
        d_lst.append({itr:val})
    return  d_lst

  # Recurse:
  else:
    key = doflsts.keys()[0]
    lst = doflsts[key]
    del doflsts[key]
    d_lst = DP(doflsts)
    new_d_lst = []
    for val in lst:
      for ds in d_lst:
        new_d = deepcopy(ds)
        new_d[key] = val
        new_d_lst.append(new_d)
    return new_d_lst

def getEvaluatedIdxAccesses(access):
  loopIters = access.iterators

  # Generate all possible accesses to the write:
  doflsts = {}
  for loopIter in loopIters:
    doflsts[loopIter.id] = range(loopIter.lower, loopIter.upper, loopIter.step)
  iter_to_vals = DP(doflsts)

  # Create list of this expression evaluated at all points:
  evaluated_expressions = []
  for iter_to_val in iter_to_vals:
    expression = deepcopy(access.indexing_exp)
    for itrid, val in iter_to_val.items(): # {i:0, j:0, k:0} mapping

      ReplaceWithConstant(val, itrid).visit(expression)
      ast.fix_missing_locations(expression)

    evaluated_expressions.append(expression)

  # Generate possible values of access to the array:
  evaluated_idx_accesses = []
  for eval_exp in evaluated_expressions:
    evaluated_idx_accesses.append(eval(compile(ast.Expression(eval_exp), filename="<ast>", mode="eval")))

  return evaluated_idx_accesses

class OutermostForLoopVisitor(ast.NodeVisitor):
  """ Visit all outermost for loops in program """
  def visit(self, node):
    if isinstance(node, ast.For):
      global PARENT_LOOP_UID, INVERSE_MAPPING
      #cprint(node, "For found")
      
      # To reference again later when injecting the Cython superset:
      INVERSE_MAPPING[PARENT_LOOP_UID] = [node, [], []]

      loop_var_id, iter_name, iter_args = node.target.id, node.iter.func.id, node.iter.args
      INVERSE_MAPPING[PARENT_LOOP_UID][1] += [loop_var_id]
      iter_start, iter_end = iter_args[0], iter_args[1]
      assert(len(iter_args) in [1,2]) # TODO: add 3 for step argument
      assert(iter_name == "xrange" or iter_name == "range")

      depth = 0
      loopVar = Iterator(loop_var_id, iter_start, iter_end, depth)
      arrVisitor = ArrayVisitor(loopVar, [], PARENT_LOOP_UID)
      PARENT_LOOP_UID += 1
      for subnode in node.body:
        arrVisitor.visit(subnode)

    else:
      self.generic_visit(node) # keep searching for for loops

def canParallelizeLoop(i):
  canParallelize = True
  for writeAccess in allWriteAccesses[i]:
    if writeAccess == -1: return False
    for otherAccess in allAccesses[i]:
      # Check if we are accessing the same array,
      #   otherwise no dependencies possible:
      if writeAccess.array_name == otherAccess.array_name and \
         writeAccess.unique_id != otherAccess.unique_id:
        #print "hello"
        writeEvaluatedAccesses = set(getEvaluatedIdxAccesses(writeAccess))
        otherEvaluatedAccesses = set(getEvaluatedIdxAccesses(otherAccess))
        if len(writeEvaluatedAccesses.intersection(otherEvaluatedAccesses)) != 0:
          print "Array accesses:\n\t", writeAccess, "and\n\t", otherAccess, "conflict"
          canParallelize = False
          return False
  return canParallelize

def getEndLine(forloopnode):
  """ Given a forloopnode, return the line number of its last line """
  end_line = forloopnode.lineno
  for subnode in ast.walk(forloopnode):
    if 'lineno' in dir(subnode):
      end_line = max(end_line, subnode.lineno)
  return end_line

def modifyColOffsets(forloopnode):
  """ indent everything by two spaces """
  for subnode in ast.walk(forloopnode):
    if 'col_offset' in dir(subnode):
      subnode.col_offset += 2

def indentAllLinesBy(s, idt):
  """ indent all lines in s by the indent amount of idt """
  lines = s.split('\n')
  for i in xrange(len(lines)):
    lines[i] = ' '*idt + lines[i] + '\n'
  return ''.join(lines)

def parallelizeLoop(i):
  global INVERSE_MAPPING, loop_node, alreadyDefined, NUM_THREADS

  # Get loop information necessary to generate the cython code
  loop_node, loopIndices, loopArrays = INVERSE_MAPPING[i]

  # Remove duplicates:
  loopIndices, loopArrays = set(loopIndices), set(loopArrays)

  cythonLoopProlog = ""
  for loopIndexId in loopIndices:
    if loopIndexId not in alreadyDefined:
      cythonLoopProlog += " "*loop_node.col_offset + \
                          "cdef int cy" + str(loopIndexId) + "\n"
      alreadyDefined.add(loopIndexId)

  for loopArrayId in loopArrays:
    if loopArrayId not in alreadyDefined:
      cythonLoopProlog += " "*loop_node.col_offset + \
                          "cdef int *cy" + str(loopArrayId) + \
                          " = <int *>malloc(len(" + str(loopArrayId) + \
                          ")*cython.sizeof(int))\n"
      alreadyDefined.add(loopArrayId)

  cythonLoopProlog += " "*loop_node.col_offset + \
                      "with nogil, parallel(num_threads="+str(NUM_THREADS)+"):"

  start_line = loop_node.lineno
  end_line = getEndLine(loop_node)

  #print start_line, end_line

  cythonizedStream = StringIO()
  AstToCython(loop_node, cythonizedStream)
  main_loop = cythonizedStream.getvalue()

  main_loop = indentAllLinesBy(main_loop, loop_node.col_offset+2)

  #print cythonLoopProlog + main_loop

  cythonizedCode = cythonLoopProlog + main_loop
  return start_line, end_line, cythonizedCode

def createParallelizedOutput(program, toParallelize):
  prolog = """
import cython
from cython import boundscheck, wraparound
from cython.parallel import prange, parallel
from libc.stdlib cimport malloc, free
"""

  #print program
  #print toParallelize

  lines = program.split('\n')
  newlines = []
  found = False
  for i in xrange(len(lines)):

    for startlineno, endlineno, code, in toParallelize:
      if startlineno-1== i:
        newlines.append(code)
        found = True
      elif endlineno == i:
        found = False

    if not found:
      newlines.append(lines[i]+'\n')

  finalCython = prolog + ''.join(newlines)
  #print finalCython
  return finalCython

if __name__ == "__main__":
  tree = ast.parse(program)

  #print "####\n", ast.dump(tree), "####\n"

  # Find all array accesses:
  allWriteAccesses = defaultdict(list)
  allAccesses = defaultdict(list)
  visitor = OutermostForLoopVisitor()
  for node in tree.body:
    visitor.visit(node)
    #print "~"*5


  #print allWriteAccesses

  # Global to keep track of line numbers of which loops to parallelize
  toParallelize = []

  # Global to keep track of which Cython variable names have already been defined
  alreadyDefined = set([])

  for i in xrange(PARENT_LOOP_UID):
    if canParallelizeLoop(i):
      toParallelize.append(parallelizeLoop(i))
      print "Loop", i, "can be parallelized."
    else:
      print "Loop", i, "cannot be parallelized."

  parallel_source = createParallelizedOutput(program, toParallelize)

  newfilename = argv[1].split('/')
  newfilename[-1] = 'par' + newfilename[-1]
  newfilename = '/'.join(newfilename)
  open(newfilename, 'w').write(parallel_source)
