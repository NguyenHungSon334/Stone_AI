"""
LLM tool schemas — passed to OpenRouter in every smart model call.
"""

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "update_customer",
            "description": (
                "Lưu thông tin khách hàng vào CRM ngay khi thu thập được. "
                "Gọi tool này khi khách cung cấp BẤT KỲ thông tin nào: "
                "tên, SĐT/Zalo, loại công trình, loại đá, hạng mục, địa điểm, "
                "khả năng xe cẩu, hoặc thời gian dự kiến. "
                "Chỉ truyền những trường vừa biết — có thể gọi nhiều lần."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Tên khách hàng",
                    },
                    "phone": {
                        "type": "string",
                        "description": "Số điện thoại hoặc Zalo (chỉ gồm số)",
                    },
                    "project_type": {
                        "type": "string",
                        "enum": ["mộ đơn", "lăng tộc"],
                        "description": "Loại công trình",
                    },
                    "stone_type": {
                        "type": "string",
                        "enum": ["xanh rêu", "xanh đen", "granite"],
                        "description": "Loại đá",
                    },
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Hạng mục: mộ, lăng thờ, cổng, lan can...",
                    },
                    "location": {
                        "type": "string",
                        "description": "Tỉnh/huyện thi công",
                    },
                    "crane_access": {
                        "type": "string",
                        "enum": ["có", "không"],
                        "description": "Xe cẩu tự hành vào tới chân công trình được không",
                    },
                    "timeline": {
                        "type": "string",
                        "description": "Thời gian dự kiến thi công (ví dụ: tháng 8/2025)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": (
                "Tìm sản phẩm đá lăng mộ trong kho Hồn Đá khi khách hỏi về "
                "giá cả, mẫu mã, kích thước, hoặc muốn so sánh sản phẩm. "
                "Luôn gọi tool này thay vì tự ước giá."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Câu hỏi hoặc từ khóa tìm kiếm (tiếng Việt)",
                    },
                    "stone_type": {
                        "type": "string",
                        "description": "Lọc theo loại đá nếu khách đã chỉ định",
                    },
                    "project_type": {
                        "type": "string",
                        "description": "Loại công trình nếu có",
                    },
                },
                "required": ["query"],
            },
        },
    },
]
