import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { extname } from "node:path";
import ts from "typescript";

// Reuse the CSS parser already shipped with Vite.
const { parse } = createRequire(import.meta.resolve("vite"))("postcss");
const maximumWeight = 500;
const weightProperties = new Set(["font-weight", "font", "font-variation-settings"]);
const inheritedWeights = /^(?:normal|lighter|inherit|initial|unset|revert(?:-layer)?)$/i;
const dynamicValue = /\b(?:var|attr|env|calc|min|max|clamp)\(/i;
const number = /^[+-]?(?:\d*\.\d+|\d+\.?\d*)(?:e[+-]?\d+)?$/i;

function isAllowed(property, value) {
  if (value === undefined) return false;
  const css = value.replace(/\/\*[\s\S]*?\*\//g, " ").trim();
  if (dynamicValue.test(css)) return false;
  if (property === "font-weight") {
    return (
      inheritedWeights.test(css) ||
      (number.test(css) && Number(css) >= 1 && Number(css) <= maximumWeight)
    );
  }
  if (property === "font-variation-settings") return !/["']wght["']/i.test(css);

  const tokens = css.replace(/(["'])(?:\\.|(?!\1)[^\\])*\1/g, "").split(/\s+/);
  return !tokens.some(
    (token) =>
      /^(?:bold(?:er)?|caption|icon|menu|message-box|small-caption|status-bar)$/i.test(token) ||
      (number.test(token) && Number(token) > maximumWeight),
  );
}

function propertyName(name) {
  return name.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase()).toLowerCase();
}

function literal(node) {
  if (!node) return undefined;
  if (ts.isStringLiteralLike(node) || ts.isNumericLiteral(node)) return node.text;
  if (ts.isJsxExpression(node) || ts.isParenthesizedExpression(node) || ts.isAsExpression(node)) {
    return literal(node.expression);
  }
  if (ts.isComputedPropertyName(node) && ts.isStringLiteralLike(node.expression)) {
    return node.expression.text;
  }
  return undefined;
}

function checkScript(file, source, report) {
  const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);

  function visit(node) {
    const line = () => tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1;
    if (
      (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) &&
      node.tagName.getText(tree) === "b"
    ) {
      report(line(), "Use a neutral element in place of <b>.");
    }
    if (ts.isPropertyAssignment(node) || ts.isJsxAttribute(node)) {
      const rawName = ts.isIdentifier(node.name) ? node.name.text : literal(node.name);
      const property = propertyName(rawName ?? "");
      if (weightProperties.has(property) && !isAllowed(property, literal(node.initializer))) {
        report(line(), `Use a static ${property} with a weight of ${maximumWeight} or less.`);
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(tree);
}

for (const file of process.argv.slice(2)) {
  const source = readFileSync(file, "utf8");
  const report = (line, message) => {
    console.error(`${file}:${line}: ${message}`);
    process.exitCode = 1;
  };
  if (extname(file) === ".css") {
    parse(source, { from: file }).walkDecls((declaration) => {
      const property = declaration.prop.toLowerCase();
      if (weightProperties.has(property) && !isAllowed(property, declaration.value)) {
        report(
          declaration.source.start.line,
          `Use a static ${property} with a weight of ${maximumWeight} or less.`,
        );
      }
    });
  } else {
    checkScript(file, source, report);
  }
}
