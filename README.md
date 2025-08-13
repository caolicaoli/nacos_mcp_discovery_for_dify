## Nacos MCP Discovery For Dify

**Author:** hbec
**Version:** 0.0.1
**Type:** Endpoint

### Description
发现 Nacos 中注册的 SSE/Streamable Http 类型 MCP Server 服务，合并所有Tool并转化为 Dify 中的Endpoint。

### Step 1 在Nacos中创建McpServer
在Nacos上创建McpServer，并添加好Tool。
![nacos mcp 1](./_assets/1.png)
![nacos mcp 2](./_assets/2.png)
![nacos mcp 3](./_assets/3.png)
![nacos mcp 4](./_assets/4.png)
![nacos mcp 5](./_assets/5.png)

### Step 2 配置Nacos
样例中会读取Nacos上的名字空间为middleware的所有Mcpserver的所有Tool，汇聚到Dify的Endpoint上。
![nacos mcp 7](./_assets/7.png)

### Step 3 配置McpEndpoint
在应用程序，比如cherrystudio上，添加上一步骤生成的Endpoint。
![nacos mcp 8](./_assets/8.png)

结果是Step2 配置条件下的所有的McpServer的所有Tool被聚合成一个Endpoint
![nacos mcp 9](./_assets/9.png)



