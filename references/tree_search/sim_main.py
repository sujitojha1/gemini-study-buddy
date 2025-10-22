import os
import re
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from google import genai
import asyncio
from rich.console import Console
from rich.panel import Panel

console = Console()

# Load environment variables and setup Gemini
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

async def generate_with_timeout(client, prompt, timeout=10):
    try:
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt
                )
            ),
            timeout=timeout
        )
        return response
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return None

async def main():
    try:
        console.print(Panel("Tree Search Reasoning Explorer", border_style="magenta"))

        server_params = StdioServerParameters(
            command="python",
            args=["cot_tools.py"]
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                system_prompt = """You are a planning agent that explores multiple reasoning paths before solving a problem.
You have access to these tools:
- explore_path(steps: list) - Show the reasoning steps for one path
- calculate(expression: str) - Calculate the result of an expression
- verify(expression: str, expected: float) - Verify if a calculation is correct
- evaluate_paths(paths: list) - Evaluate reasoning paths and recommend the best one

First, generate 3 different reasoning paths for solving the problem.
Then explore the most promising one in steps using function calls.

Respond with EXACTLY ONE line in one of these formats:
1. FUNCTION_CALL: function_name|param1|param2|...
2. FINAL_ANSWER: [answer]
(For evaluate_paths, use FUNCTION_CALL: evaluate_paths|[\"Path 1\", \"Path 2\", \"Path 3\"])"""

                problem = "(23 + 7) * (15 - 8)"
                console.print(Panel(f"Problem: {problem}", border_style="cyan"))

                prompt = f"{system_prompt}\n\nGenerate 3 different paths for solving: {problem}"

                response = await generate_with_timeout(client, prompt)
                if not response or not response.text:
                    return

                result = response.text.strip()
                console.print(f"\n[yellow]Assistant:[/yellow] {result}")

                match = re.search(r'FUNCTION_CALL:\s*(\w+)\|(.+)', result)
                if not match:
                    console.print("[red]No FUNCTION_CALL found in model response[/red]")
                    return

                func_name = match.group(1)
                raw_args = match.group(2)

                if func_name == "explore_path":
                    path_list = eval(raw_args)
                    await session.call_tool("explore_path", arguments={"steps": path_list})
                    chosen_path = await session.call_tool("evaluate_paths", arguments={"paths": path_list})
                elif func_name == "evaluate_paths":
                    path_list = eval(raw_args)
                    await session.call_tool("explore_path", arguments={"steps": path_list})
                    chosen_path = await session.call_tool("evaluate_paths", arguments={"paths": path_list})
                else:
                    console.print(f"[red]Unknown function call: {func_name}[/red]")
                    return

                if not chosen_path or not chosen_path.content:
                    console.print("[red]No content returned from evaluate_paths[/red]")
                    return

                content_text = chosen_path.content[0].text.strip()
                console.print(f"[bold green]Best Path Returned by Tool:[/bold green] {content_text}")

                match = re.search(r'Path (\d+)', content_text)
                if not match:
                    console.print("[red]Could not extract best path index from tool output[/red]")
                    return

                best_path_index = int(match.group(1)) - 1
                chosen_path_text = path_list[best_path_index]

                console.print(f"\n[yellow]Assistant:[/yellow] Decomposing: {chosen_path_text}")
                decompose_prompt = (
                    f"Decompose this into step-by-step calculations: {chosen_path_text} for problem: {problem}.\n"
                    "Respond in format: FUNCTION_CALL: show_reasoning|[\"step 1\", \"step 2\", ...]"
                )
                response = await generate_with_timeout(client, decompose_prompt)
                if not response or not response.text:
                    return

                result = response.text.strip()
                console.print(f"[yellow]Decomposition Response:[/yellow] {result}")

                lines = result.splitlines()
                steps = []

                for line in lines:
                    if "show_reasoning|" in line:
                        try:
                            part = line.split("show_reasoning|", 1)[1].strip()
                            extracted_steps = eval(part)
                            steps.extend(extracted_steps)
                        except Exception as e:
                            console.print(f"[red]Failed to parse step line: {line} — {e}[/red]")
                    elif "[" in line and "]" in line and any(op in line for op in ["+", "-", "*", "/"]):
                        try:
                            extracted = eval(line.split("|", 1)[1].strip()) if "|" in line else eval(line.strip())
                            if isinstance(extracted, list):
                                steps.extend(extracted)
                        except Exception as e:
                            console.print(f"[red]Fallback parse failed: {line} — {e}[/red]")
                    elif re.match(r'\s*\d+\s*[+\-*/]\s*\d+\s*=\s*\d+', line):
                        steps.append(line.strip())

                if steps:
                    await session.call_tool("show_reasoning", arguments={"steps": steps})
                    console.print(f"[blue]Steps to execute:[/blue] {steps}")

                    for step in steps:
                        match = re.search(r'([\d\s\+\-\*/\(\)]+)', step)
                        if match:
                            expr = match.group(1).strip()
                            calc = await session.call_tool("calculate", arguments={"expression": expr})
                            if calc and calc.content:
                                value_text = calc.content[0].text.strip()
                                match = re.search(r'(-?\d+(\.\d+)?)', value_text)
                                if match:
                                    value = float(match.group(1))
                                    await session.call_tool("verify", arguments={"expression": expr, "expected": value})
                                else:
                                    console.print(f"[red]Could not extract a numeric value from:[/red] {value_text}")


                    # ✅ FINAL_ANSWER extraction
                    final_step = steps[-1].strip()
                    match = re.search(r'=\s*(\d+(\.\d+)?)', final_step)
                    if match:
                        final_answer = match.group(1)
                        console.print(f"\n[bold cyan]FINAL_ANSWER: {final_answer}[/bold cyan]")

                    console.print("[green]Best path executed successfully![/green]")
                    console.print("[bold green]🎉 All reasoning steps executed and verified successfully![/bold green]")

                else:
                    console.print("[red]No valid steps extracted from decomposition response.[/red]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

if __name__ == "__main__":
    asyncio.run(main())