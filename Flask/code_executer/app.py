from flask import Flask, request, jsonify
import docker
import os
import uuid
import time
import datetime
from flask_cors import CORS

app = Flask(__name__)

# Enable CORS for the app
CORS(app)

# Initialize Docker client
client = docker.from_env()

# Path to save temporary files
CODE_DIR = './temp_code/'

# Create the directory if it doesn't exist
if not os.path.exists(CODE_DIR):
    os.makedirs(CODE_DIR)

@app.route('/execute', methods=['POST'])
def execute_code():
    data = request.json
    language = data.get('language')
    code = data.get('code')

    if language == 'python':
        result = run_python_code(code)
    elif language == 'java':
        result = run_java_code(code)
    else:
        return jsonify({'error': 'Unsupported language'}), 400

    return jsonify(result)

def run_python_code(code):
    file_id = str(uuid.uuid4())
    file_path = os.path.join(CODE_DIR, f'{file_id}.py')

    # Save the Python code to a file
    with open(file_path, 'w') as code_file:
        code_file.write(code)

    try:
        # Record the start time
        start_time = time.time()

        # Run the Python code in a Docker container
        container = client.containers.run(
            image='python:3.9-slim',
            command=f'python /code/{file_id}.py',
            remove=False,  # Don't remove the container to capture stats
            volumes={os.path.abspath(CODE_DIR): {'bind': '/code', 'mode': 'rw'}},
            detach=True  # Run in the background
        )

        # Wait for the container to finish and capture the logs
        container.wait()
        output = container.logs().decode('utf-8')

        # Get memory usage and execution time
        memory_usage, execution_time, stats = get_container_stats(container, start_time)

        # Remove the container
        container.remove()

        return {
            'output': output,
            'execution_time': execution_time,
            'memory_usage': memory_usage,
            'stats': stats  # Add stats for more detailed output (like CPU, I/O, etc.)
        }
    except docker.errors.ContainerError as e:
        return {'error': e.stderr.decode('utf-8')}

def run_java_code(code):
    file_path = os.path.join(CODE_DIR, 'Solution.java')

    # Save the Java code to a file named Solution.java
    with open(file_path, 'w') as code_file:
        code_file.write(code)

    try:
        # Record the start time
        start_time = time.time()

        # Compile the Java code
        client.containers.run(
            image='openjdk:11-jdk-slim',
            command='javac /code/Solution.java',
            remove=True,
            volumes={os.path.abspath(CODE_DIR): {'bind': '/code', 'mode': 'rw'}}
        )

        # Run the compiled Java code
        container = client.containers.run(
            image='openjdk:11-jdk-slim',
            command='java -cp /code Solution',
            remove=False,
            volumes={os.path.abspath(CODE_DIR): {'bind': '/code', 'mode': 'rw'}},
            detach=True  # Run in the background
        )

        # Wait for the container to finish and capture the logs
        container.wait()
        output = container.logs().decode('utf-8')

        # Get memory usage and execution time
        memory_usage, execution_time, stats = get_container_stats(container, start_time)

        # Remove the container
        container.remove()

        # Save the Java file with a timestamp after successful execution
        save_code_with_timestamp(code)

        return {
            'output': output,
            'execution_time': execution_time,
            'memory_usage': memory_usage,
            'stats': stats  # Add stats for more detailed output (like CPU, I/O, etc.)
        }
    except docker.errors.ContainerError as e:
        return {'error': e.stderr.decode('utf-8')}

def save_code_with_timestamp(code):
    """
    Save the Java code file with a timestamp after successful execution.
    """
    # Generate a timestamp
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

    # Define the file path with the timestamp
    file_path_with_timestamp = os.path.abspath(os.path.join(CODE_DIR, '../saved_code', f'Solution_{timestamp}.java'))

    # Save the code to the new file
    with open(file_path_with_timestamp, 'w') as code_file:
        code_file.write(code)

    print(f"Java file saved as: {file_path_with_timestamp}")

def get_container_stats(container, start_time):
    """
    Get detailed stats including memory usage, CPU percentage, network IO, and block IO for the container.
    """
    try:
        time.sleep(0.5)  # Small delay to ensure Docker collects stats for short-lived containers

        # Get container stats
        stats = container.stats(stream=False)

        print("Raw Docker Stats:", stats)

        # Extract memory usage and limit
        memory_usage = stats.get('memory_stats', {}).get('usage', 0) / (1024 * 1024)  # Convert to MB
        memory_limit = stats.get('memory_stats', {}).get('limit', 1) / (1024 * 1024)  # Convert to MB
        memory_percentage = (memory_usage / memory_limit) * 100 if memory_limit else 0

        # Extract CPU percentage
        cpu_percentage = calculate_cpu_percent(stats)

        # Extract network IO
        net_io = 0
        if 'networks' in stats:
            for interface, data in stats['networks'].items():
                net_io += data['rx_bytes'] + data['tx_bytes']

        # Extract block IO
        block_io = 0
        if 'blkio_stats' in stats and stats['blkio_stats'].get('io_service_bytes_recursive'):
            for entry in stats['blkio_stats']['io_service_bytes_recursive']:
                block_io += entry.get('value', 0)

        # Extract the number of PIDs
        pids = stats.get('pids_stats', {}).get('current', 'Not available')

        # Calculate execution time
        execution_time = time.time() - start_time

        return {
            'memory_usage': memory_usage,
            'memory_percentage': memory_percentage,
            'cpu_percentage': cpu_percentage,
            'net_io': net_io,
            'block_io': block_io,
            'pids': pids,
            'execution_time': execution_time
        }
    except KeyError as e:
        print(f"KeyError in stats: {e}")
        return None, time.time() - start_time, {}  # Return empty stats if there's a KeyError

def calculate_cpu_percent(stats):
    """
    Calculate the CPU percentage used by the container based on Docker stats.
    """
    cpu_usage = stats['cpu_stats']['cpu_usage']['total_usage']
    precpu_usage = stats['precpu_stats']['cpu_usage']['total_usage']
    system_cpu_usage = stats['cpu_stats']['system_cpu_usage']
    system_precpu_usage = stats['precpu_stats']['system_cpu_usage']

    cpu_delta = cpu_usage - precpu_usage
    system_delta = system_cpu_usage - system_precpu_usage

    num_cpus = stats['cpu_stats']['online_cpus']

    if system_delta > 0 and cpu_delta > 0:
        cpu_percentage = (cpu_delta / system_delta) * num_cpus * 100
    else:
        cpu_percentage = 0.0

    return cpu_percentage

if __name__ == '__main__':
    app.run(debug=True)
