from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
import math
import re

console = Console(stderr=True)
mcp = FastMCP("CoTCalculator")

@mcp.tool()
def show_reasoning(steps: list) -> TextContent:
    """Show the step-by-step reasoning process"""
    console.print("[blue]FUNCTION CALL:[/blue] show_reasoning()")
    for i, step in enumerate(steps, 1):
        console.print(Panel(
            f"{step}",
            title=f"Step {i}",
            border_style="cyan"
        ))
    return TextContent(
        type="text",
        text="Reasoning shown"
    )

@mcp.tool()
def calculate(expression: str) -> TextContent:
    """Calculate the result of an expression"""
    console.print("[blue]FUNCTION CALL:[/blue] calculate()")
    console.print(f"[blue]Expression:[/blue] {expression}")
    try:
        result = eval(expression)
        console.print(f"[green]Result:[/green] {result}")
        return TextContent(
            type="text",
            text=str(result)
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        return TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )

@mcp.tool()
def verify(expression: str, expected: float) -> TextContent:
    """Verify if a calculation is correct"""
    console.print("[blue]FUNCTION CALL:[/blue] verify()")
    console.print(f"[blue]Verifying:[/blue] {expression} = {expected}")
    try:
        actual = float(eval(expression))
        is_correct = abs(actual - float(expected)) < 1e-10
        
        if is_correct:
            console.print(f"[green]✓ Correct! {expression} = {expected}[/green]")
        else:
            console.print(f"[red]✗ Incorrect! {expression} should be {actual}, got {expected}[/red]")
            
        return TextContent(
            type="text",
            text=str(is_correct)
        )
    except Exception as e:
        console.print(f"[red]Error:[/red] {str(e)}")
        return TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )

@mcp.tool()
def check_consistency(steps: list) -> TextContent:
    """Check if calculation steps are consistent with each other"""
    console.print("[blue]FUNCTION CALL:[/blue] check_consistency()")
    
    try:
        # Create a table for step analysis
        table = Table(
            title="Step-by-Step Consistency Analysis",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan"
        )
        table.add_column("Step", style="cyan")
        table.add_column("Expression", style="blue")
        table.add_column("Result", style="green")
        table.add_column("Checks", style="yellow")

        issues = []
        warnings = []
        insights = []
        previous = None
        
        for i, (expression, result) in enumerate(steps, 1):
            checks = []
            
            # 1. Basic Calculation Verification
            try:
                expected = eval(expression)
                if abs(float(expected) - float(result)) < 1e-10:
                    checks.append("[green]✓ Calculation verified[/green]")
                else:
                    issues.append(f"Step {i}: Calculation mismatch")
                    checks.append("[red]✗ Calculation error[/red]")
            except:
                warnings.append(f"Step {i}: Couldn't verify calculation")
                checks.append("[yellow]! Verification failed[/yellow]")

            # 2. Dependency Analysis
            if previous:
                prev_expr, prev_result = previous
                if str(prev_result) in expression:
                    checks.append("[green]✓ Uses previous result[/green]")
                    insights.append(f"Step {i} builds on step {i-1}")
                else:
                    checks.append("[blue]○ Independent step[/blue]")

            # 3. Magnitude Check
            if previous and result != 0 and previous[1] != 0:
                ratio = abs(result / previous[1])
                if ratio > 1000:
                    warnings.append(f"Step {i}: Large increase ({ratio:.2f}x)")
                    checks.append("[yellow]! Large magnitude increase[/yellow]")
                elif ratio < 0.001:
                    warnings.append(f"Step {i}: Large decrease ({1/ratio:.2f}x)")
                    checks.append("[yellow]! Large magnitude decrease[/yellow]")

            # 4. Pattern Analysis
            operators = re.findall(r'[\+\-\*\/\(\)]', expression)
            if '(' in operators and ')' not in operators:
                warnings.append(f"Step {i}: Mismatched parentheses")
                checks.append("[red]✗ Invalid parentheses[/red]")

            # 5. Result Range Check
            if abs(result) > 1e6:
                warnings.append(f"Step {i}: Very large result")
                checks.append("[yellow]! Large result[/yellow]")
            elif abs(result) < 1e-6 and result != 0:
                warnings.append(f"Step {i}: Very small result")
                checks.append("[yellow]! Small result[/yellow]")

            # Add row to table
            table.add_row(
                f"Step {i}",
                expression,
                f"{result}",
                "\n".join(checks)
            )
            
            previous = (expression, result)

        # Display Analysis
        console.print("\n[bold cyan]Consistency Analysis Report[/bold cyan]")
        console.print(table)

        if issues:
            console.print(Panel(
                "\n".join(f"[red]• {issue}[/red]" for issue in issues),
                title="Critical Issues",
                border_style="red"
            ))

        if warnings:
            console.print(Panel(
                "\n".join(f"[yellow]• {warning}[/yellow]" for warning in warnings),
                title="Warnings",
                border_style="yellow"
            ))

        if insights:
            console.print(Panel(
                "\n".join(f"[blue]• {insight}[/blue]" for insight in insights),
                title="Analysis Insights",
                border_style="blue"
            ))

        # Final Consistency Score
        total_checks = len(steps) * 5  # 5 types of checks per step
        passed_checks = total_checks - (len(issues) * 2 + len(warnings))
        consistency_score = (passed_checks / total_checks) * 100

        console.print(Panel(
            f"[bold]Consistency Score: {consistency_score:.1f}%[/bold]\n" +
            f"Passed Checks: {passed_checks}/{total_checks}\n" +
            f"Critical Issues: {len(issues)}\n" +
            f"Warnings: {len(warnings)}\n" +
            f"Insights: {len(insights)}",
            title="Summary",
            border_style="green" if consistency_score > 80 else "yellow" if consistency_score > 60 else "red"
        ))

        return TextContent(
            type="text",
            text=str({
                "consistency_score": consistency_score,
                "issues": issues,
                "warnings": warnings,
                "insights": insights
            })
        )
    except Exception as e:
        console.print(f"[red]Error in consistency check: {str(e)}[/red]")
        return TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )

@mcp.tool()
def explore_path(steps: list) -> TextContent:
    """Simulate reasoning steps for a given path"""
    console.print("[magenta]FUNCTION CALL:[/magenta] explore_path()")
    for i, step in enumerate(steps, 1):
        console.print(Panel(f"{step}", title=f"Path Step {i}", border_style="magenta"))
    return TextContent(type="text", text="Path explored.")

@mcp.tool()
def evaluate_paths(paths: list) -> TextContent:
    """Evaluate reasoning paths and rank them"""
    console.print("[magenta]FUNCTION CALL:[/magenta] evaluate_paths()")
    scored = []
    for i, path in enumerate(paths, 1):
        score = 100 - len(path) * 5  # toy heuristic
        scored.append((f"Path {i}", path, score))

    console.print("\n[bold magenta]Path Evaluation Summary[/bold magenta]")
    for name, path, score in scored:
        console.print(Panel(
            f"Score: {score}/100\nReasoning: {path}",
            title=name,
            border_style="green" if score >= 90 else "yellow"
        ))

    best = max(scored, key=lambda x: x[2])
    console.print(Panel(
        f"[bold]Recommended Path:[/bold] {best[0]}\nScore: {best[2]}",
        title="Best Option",
        border_style="green"
    ))

    # ✅ THIS IS CRITICAL
    return TextContent(type="text", text=best[0])



if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "dev":
        mcp.run()
    else:
        mcp.run(transport="stdio")
