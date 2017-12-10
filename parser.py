#!/bin/env python
import argparse
import pathlib
import ast
import re
from collections import namedtuple
from enum import Enum
from io import StringIO

IdentifierType = Enum('IdentifierType', 
  'PARAM_DOUBLE PARAM_SINGLE_R PARAM_SINGLE_L DRAIN_OUT DRAIN_IN INITIAL_COND'
)

def is_numeric(number):
    try: 
        float(number)
    except ValueError:
        try:
            int(number)
        except ValueError:
            return False
        return True
    return True

class ParsedDictResults:
  def __init__(self, initial=None, params=None, drain=None):
    self.initial = dict() if initial is None else initial
    self.params = dict() if params is None else params
    self.drain = dict() if drain is None else drain

  def __repr__(self):
    return "<ParsedDictResults initial={} params={} drain={}>".format(self.initial, self.params, self.drain)

class ParseError(Exception):
  @staticmethod
  def formatted(lineno, message):
    return ParseError("Error at line {}: {}".format(lineno, message))

class Identifier:
  def __init__(self, identifier_type, lsymbols, rsymbols=None):
    self.type = identifier_type
    self.left_symbols = lsymbols
    self.right_symbols = rsymbols

  def stringify_reaction(self):
    with StringIO() as out:
      if self.type == IdentifierType.PARAM_DOUBLE:
        out.write(" + ".join(self.left_symbols))
        out.write(" <=> ")
        out.write(" + ".join(self.right_symbols))
      elif self.type == IdentifierType.PARAM_SINGLE_R:
        out.write(" + ".join(self.left_symbols))
        out.write(" -> ")
        out.write(" + ".join(self.right_symbols))
      elif self.type == IdentifierType.PARAM_SINGLE_L:
        out.write(" + ".join(self.right_symbols))
        out.write(" -> ")
        out.write(" + ".join(self.left_symbols))
        
      return out.getvalue()

  @staticmethod
  def parse_identifier(identifier_type, lineno, raw):

    def sort_out(symbols):
      if symbols is None:
        return
      iterator = iter(symbols)
      for sym in iterator:
        if sym.isdecimal():
          try: 
            symbol_to_mutiple = next(iterator)
            for _ in range(int(sym)):
              yield symbol_to_mutiple
          except ValueError:
            raise ParseError.formatted(lineno, "Invalid multiplier") 
        elif sym.isalpha():
          yield sym
        elif sym == "+" or sym == "->" or sym == "<=>" or sym == "<-":
          continue
        else:
          raise ParseError.formatted(lineno, "Invalid identifier symbol '{}'".format(sym))

    if identifier_type == IdentifierType.DRAIN_OUT or \
      identifier_type == IdentifierType.DRAIN_IN:
      symbols = tuple(sort_out(raw.split()))
      if len(symbols) != 1:
        raise ParseError.formatted(lineno, "Drain parameter statements does not support multipliers")
      return Identifier(identifier_type, symbols)
    if identifier_type == IdentifierType.INITIAL_COND:
      symbols = tuple(sort_out(raw.split()))
      if len(symbols) == 0:
        raise ParseError.formatted(lineno, "No symbols supplied")
      return Identifier(identifier_type, symbols)
    if identifier_type == IdentifierType.PARAM_DOUBLE:
      left_symbols, _ , right_symbols = raw.partition('<=>')
    if identifier_type == IdentifierType.PARAM_SINGLE_R:
      left_symbols, _ , right_symbols = raw.partition('->')
    if identifier_type == IdentifierType.PARAM_SINGLE_L:
      left_symbols, _ , right_symbols = raw.partition('<-')

    left_symbols = left_symbols.split()
    right_symbols = right_symbols.split()
    if len(left_symbols) == len(right_symbols) and len(left_symbols) == 0:
      raise ParseError.formatted(lineno, "Empty symbol declaration")
    return Identifier(identifier_type, tuple(sort_out(left_symbols)), tuple(sort_out(right_symbols)))

  def __repr__(self):
    return "<Identifier {} ls:{} rs:{}>".format(self.type, self.left_symbols, self.right_symbols)

def cooked_lines(file_descriptor):
  yield from ((i, l) for i, l in ((lineno, raw.strip()) for lineno, raw in enumerate(file_descriptor)) if len(l) > 0 and not l.startswith('#'))

def get_name_val(line, line_number, seperator=':'):
  name, sep, val = line.partition(seperator)
  if len(sep) == 0:
    raise ParseError.formatted(line_number, "Unexpected statement")
  return map(str.strip, (name, val))

def parse_value(value, line_number):
  if "," in value:
    def parse_plural_value(values):
      for v in (raw.strip() for raw in values):
        if not is_numeric(v):
          raise ParseError.formatted(line_number, "Non-numeric value is not allowed")
        yield ast.literal_eval(v)
    return tuple(parse_plural_value(value.split(",")))
  return ast.literal_eval(value.strip()),

def parse_name(name, line_number):

  def get_identifier_type(name):
    if name.startswith('->'):
      return IdentifierType.DRAIN_IN
    elif name.startswith('<-'):
      return IdentifierType.DRAIN_OUT
    elif "<=>" in name:
      return IdentifierType.PARAM_DOUBLE
    elif "->" in name:
      return IdentifierType.PARAM_SINGLE_R
    elif "<-" in name:
      return IdentifierType.PARAM_SINGLE_L
    else:
      return IdentifierType.INITIAL_COND

  return Identifier.parse_identifier(get_identifier_type(name), line_number, name)


def parse_args():
  class PathAction(argparse.Action):
    def __call__(self, parser, namespace, values, opt_string):
      setattr(namespace, "_".join(self.dest.split(' ')), pathlib.Path(values[0]))

  parser = argparse.ArgumentParser(description="Compiler for simulation library")
  parser.add_argument('input file', nargs=1, action=PathAction, help="input files")
  parser.add_argument('-o', '--output', nargs=1, action=PathAction, help="output file")
  return parser.parse_args()

def parse_file(fd):
  for line_number, line in cooked_lines(fd):
    name, val = get_name_val(line, line_number)
    parsed_name, parsed_value = parse_name(name, line_number), parse_value(val, line_number)
    yield line_number, parsed_name, parsed_value

def get_dicts(parsed):
  result = ParsedDictResults()
  for line_number, identifier, value in parsed:
    if identifier.type == IdentifierType.INITIAL_COND:
      if len(value) > 1:
        raise ParseError.formatted(line_number, "Initial condition declaration expects 1 value")
      result.initial[identifier.left_symbols[0]] = value[0]

    elif identifier.type == IdentifierType.PARAM_DOUBLE:
      if len(value) == 1:
        result.params[identifier.stringify_reaction()] = value[0]
      elif len(value) == 2:
        result.params[identifier.stringify_reaction()] = value
      else:
        raise ParseError.formatted(line_number, "Too many values declared for two-way reaction")
    elif identifier.type == IdentifierType.PARAM_SINGLE_L or identifier.type == IdentifierType.PARAM_SINGLE_R:
      if len(value) != 1:
        raise ParseError.formatted(line_number, "Too many values declared for one-way reaction")
      result.params[identifier.stringify_reaction()] = value[0]
    elif identifier.type == IdentifierType.DRAIN_OUT:
      if len(value) != 1:
        raise ParseError.formatted(line_number, "Too many values declared for drain parameter")
      try:
        result.drain[identifier.left_symbols[0]]['out'] = { 'factor': value[0] }
      except KeyError:
        result.drain[identifier.left_symbols[0]] = { 'out': { 'factor': value[0] } }
    elif identifier.type == IdentifierType.DRAIN_IN:
      if len(value) != 1:
        raise ParseError.formatted(line_number, "Too many values declared for drain parameter")
      try:
        result.drain[identifier.left_symbols[0]]['in'] = { 'constant': value[0] }
      except KeyError:
        result.drain[identifier.left_symbols[0]] = { 'in': { 'constant': value[0] } }
  return result


def main(argv):
  with argv.input_file.open() as f:
    parsed = parse_file(f)
    result = get_dicts(parsed)

  print(result)


if __name__ == '__main__':
  main(parse_args())