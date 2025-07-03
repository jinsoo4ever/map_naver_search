def append_info_row_to_excel(search_keyword, page, index, info: dict, output_path: str):
    import pandas as pd
    import os

    row = {
        "검색어": search_keyword,
        "페이지": page,
        "인덱스": index + 1,
        "name": info.get("name"),
        "address": info.get("address"),
        "lot_number": info.get("lot_number"),
        "phone": info.get("phone"),
        "homepage": info.get("homepage"),
        "share": info.get("share"),
        "intro": info.get("intro"),
        "main_keyword": ", ".join(info.get("main_keyword", [])),
    }

    df = pd.DataFrame([row])

    # 엑셀이 존재하지 않으면 헤더 포함 저장, 존재하면 이어쓰기
    if not os.path.exists(output_path):
        df.to_excel(output_path, index=False)
    else:
        with pd.ExcelWriter(output_path, engine='openpyxl', mode='a', if_sheet_exists='overlay') as writer:
            df.to_excel(writer, index=False, header=False, startrow=writer.sheets['Sheet1'].max_row)

    #print(f"[INFO] 데이터 저장됨: {output_path}")