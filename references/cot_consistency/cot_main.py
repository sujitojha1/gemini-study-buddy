import os
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
    """Generate content with a timeout"""
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

async def get_llm_response(client, prompt):
    """Get response from LLM with timeout"""
    response = await generate_with_timeout(client, prompt)
    if response and response.text:
        return response.text.strip()
    return None

async def main():
    try:
        console.print(Panel("Chain of Thought Calculator", border_style="cyan"))

        server_params = StdioServerParameters(
            command="python",
            args=["cot_tools.py"]
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                system_prompt = """You are a mathematical reasoning agent that solves problems step by step.
You have access to these tools:
- show_reasoning(steps: list) - Show your step-by-step reasoning process
- calculate(expression: str) - Calculate the result of an expression
- verify(expression: str, expected: float) - Verify if a calculation is correct

First show your reasoning, then calculate and verify each step.

Respond with EXACTLY ONE line in one of these formats:
1. FUNCTION_CALL: function_name|param1|param2|...
2. FINAL_ANSWER: [answer]

Example:
User: Solve (2 + 3) * 4
Assistant: FUNCTION_CALL: show_reasoning|["1. First, solve inside parentheses: 2 + 3", "2. Then multiply the result by 4"]
User: Next step?
Assistant: FUNCTION_CALL: calculate|2 + 3
User: Result is 5. Let's verify this step.
Assistant: FUNCTION_CALL: verify|2 + 3|5
User: Verified. Next step?
Assistant: FUNCTION_CALL: calculate|5 * 4
User: Result is 20. Let's verify the final answer.
Assistant: FUNCTION_CALL: verify|(2 + 3) * 4|20
User: Verified correct.
Assistant: FINAL_ANSWER: [20]"""

                problem = "(23 + 7) * (15 - 8)"
                console.print(Panel(f"Problem: {problem}", border_style="cyan"))

                # Initialize conversation
                prompt = f"{system_prompt}\n\nSolve this problem step by step: {problem}"
                conversation_history = []

                while True:
                    response = await generate_with_timeout(client, prompt)
                    if not response or not response.text:
                        break

                    result = response.text.strip()
                    console.print(f"\n[yellow]Assistant:[/yellow] {result}")

                    if result.startswith("FUNCTION_CALL:"):
                        _, function_info = result.split(":", 1)
                        parts = [p.strip() for p in function_info.split("|")]
                        func_name = parts[0]
                        
                        if func_name == "show_reasoning":
                            steps = eval(parts[1])
                            await session.call_tool("show_reasoning", arguments={"steps": steps})
                            prompt += f"\nUser: Next step?"
                            
                        elif func_name == "calculate":
                            expression = parts[1]
                            calc_result = await session.call_tool("calculate", arguments={"expression": expression})
                            if calc_result.content:
                                value = calc_result.content[0].text
                                prompt += f"\nUser: Result is {value}. Let's verify this step."
                                conversation_history.append((expression, float(value)))
                                
                        elif func_name == "verify":
                            expression, expected = parts[1], float(parts[2])
                            await session.call_tool("verify", arguments={
                                "expression": expression,
                                "expected": expected
                            })
                            prompt += f"\nUser: Verified. Next step?"
                            
                    elif result.startswith("FINAL_ANSWER:"):
                        # Verify the final answer against the original problem
                        if conversation_history:
                            final_answer = float(result.split("[")[1].split("]")[0])
                            await session.call_tool("verify", arguments={
                                "expression": problem,
                                "expected": final_answer
                            })
                        break
                    
                    prompt += f"\nAssistant: {result}"

                console.print("\n[green]Calculation completed![/green]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

if __name__ == "__main__":
    asyncio.run(main())
