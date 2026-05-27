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
            "name": "get_product_detail",
            "description": (
                "Lấy toàn bộ thông tin chi tiết của 1 sản phẩm theo mã sản phẩm. "
                "Gọi khi khách hỏi cụ thể về 1 sản phẩm (kích thước, mô tả, khối lượng...)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ma_sp": {
                        "type": "string",
                        "description": "Mã sản phẩm (ví dụ: LD01, M05)",
                    },
                },
                "required": ["ma_sp"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price",
            "description": (
                "Lấy giá theo từng loại đá của sản phẩm. "
                "Gọi khi khách hỏi giá cụ thể của 1 sản phẩm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ma_sp": {
                        "type": "string",
                        "description": "Mã sản phẩm",
                    },
                    "loai_da": {
                        "type": "string",
                        "description": "Loại đá (xanh đen, xanh rêu, xám, granite). Bỏ trống để lấy tất cả.",
                    },
                },
                "required": ["ma_sp"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_media",
            "description": (
                "Lấy ảnh và video của sản phẩm để gửi cho khách xem. "
                "Gọi khi khách muốn xem ảnh, hình mẫu sản phẩm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ma_sp": {
                        "type": "string",
                        "description": "Mã sản phẩm",
                    },
                    "loai": {
                        "type": "string",
                        "enum": ["ảnh", "video", "tất cả"],
                        "description": "Loại media cần lấy",
                    },
                },
                "required": ["ma_sp"],
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
                        "description": "Lọc theo loại đá nếu khách đã chỉ định (xanh rêu, xanh đen, granite)",
                    },
                    "project_type": {
                        "type": "string",
                        "description": "Loại sản phẩm cụ thể như khách nêu, bao gồm hình dạng nếu có. Ví dụ: 'mộ tròn', 'mộ 2 cấp', 'long đình', 'cổng'. KHÔNG tự suy ra thành 'mộ đơn' khi khách nói 'mộ tròn'.",
                    },
                    "budget": {
                        "type": "string",
                        "description": "Ngân sách tối đa nếu khách đề cập (ví dụ: '50 triệu', '200tr')",
                    },
                    "chieu_dai": {
                        "type": "string",
                        "description": "Chiều dài tối đa yêu cầu (ví dụ: '1200mm', '1.2m')",
                    },
                    "chieu_cao": {
                        "type": "string",
                        "description": "Chiều cao tối đa yêu cầu",
                    },
                    "chieu_rong": {
                        "type": "string",
                        "description": "Chiều rộng tối đa yêu cầu",
                    },
                },
                "required": ["query"],
            },
        },
    },
]
