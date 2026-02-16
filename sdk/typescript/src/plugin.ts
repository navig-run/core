export class NavigPlugin {
    private commands: Map<string, Function> = new Map();

    constructor(public name: string) { }

    command(name: string, handler: Function) {
        this.commands.set(name, handler);
    }

    run() {
        process.stdin.on("data", (data) => {
            // JSON-RPC handling based on stdio
            const message = JSON.parse(data.toString());
            if (this.commands.has(message.method)) {
                const result = this.commands.get(message.method)!(message.params);
                console.log(JSON.stringify({ id: message.id, result }));
            }
        });
    }
}
